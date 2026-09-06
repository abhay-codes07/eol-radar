#!/usr/bin/env -S rote play run
/**
 * @rote-frontmatter
 * ---
 * name: eol-radar
 * description: "One job: what in this repository stops working, and on what date. Reads runtime pins, base images, CI runners and actions (resolved to the Node runtime their action.yml really declares, so SHA pins are caught), cloud runtimes and packages, then prints everything with a death date, soonest first, with the exact version to move to. Outdated is not the same as dead: this is about dates a platform enforces, not the newest release. Read-only, no credentials, and the whole scanner ships inside this package: nothing is fetched at run time."
 * source: https://github.com/abhay-codes07/eol-radar
 * provenance:
 *   author: abhay-codes07 <abhaysingh0293@gmail.com>
 * parameters:
 *   - name: root
 *     type: string
 *     required: false
 *     default: '.'
 *     description: 'Repository to scan: a local path. Defaults to the current directory.'
 *   - name: horizon_days
 *     type: string
 *     required: false
 *     default: '90'
 *     description: 'Anything that loses support or is blocked by its platform within this many days counts as dying.'
 *   - name: max_packages
 *     type: string
 *     required: false
 *     default: '300'
 *     description: 'Cap on package versions queried against deps.dev and npm; direct dependencies come first.'
 *   - name: fail_on
 *     type: string
 *     required: false
 *     default: 'none'
 *     description: 'none, dying or dead. The run fails when a finding of that severity exists, for use as a CI gate.'
 * metadata:
 *   version: 0.1.3
 *   rote_version: "0.80.0"
 *   status: draft
 *   kind: atomic
 *   flow_type: parallel
 *   execution_model: steps_with_presentation
 *   requires_sessions: false
 *   discoverability:
 *     tags:
 *     - end-of-life
 *     - eol
 *     - deprecation
 *     - lifecycle
 *     - github-actions
 *     - node20
 *     - docker
 *     - lambda
 *     - dependencies
 *     - read-only
 *   hardcode_audit:
 *     schema: 2
 *     suspicion_count: 3
 *     audit_sha256: eaf0312fac5c0311a3d46943c98467980c3edaf513025c7babeda9d88922d5fb
 * presentation_fixtures:
 *   scan_runtimes: resources/presentation-fixtures/scan_runtimes/fixture.yaml
 *   scan_containers: resources/presentation-fixtures/scan_containers/fixture.yaml
 *   scan_ci: resources/presentation-fixtures/scan_ci/fixture.yaml
 *   scan_cloud: resources/presentation-fixtures/scan_cloud/fixture.yaml
 *   scan_packages: resources/presentation-fixtures/scan_packages/fixture.yaml
 *   resolve: resources/presentation-fixtures/resolve/fixture.yaml
 *   join: resources/presentation-fixtures/join/fixture.yaml
 * steps:
 *   scan_runtimes:
 *     type: process.exec
 *     timeout_ms: 60000
 *     argv:
 *     - python3
 *     - -B
 *     - '@resource{scripts/scan_runtimes.py}'
 *     - --root
 *     - $root
 *     - --out
 *     - work/runtimes.json
 *   scan_containers:
 *     type: process.exec
 *     timeout_ms: 60000
 *     argv:
 *     - python3
 *     - -B
 *     - '@resource{scripts/scan_containers.py}'
 *     - --root
 *     - $root
 *     - --out
 *     - work/containers.json
 *   scan_ci:
 *     type: process.exec
 *     timeout_ms: 60000
 *     argv:
 *     - python3
 *     - -B
 *     - '@resource{scripts/scan_ci.py}'
 *     - --root
 *     - $root
 *     - --out
 *     - work/ci.json
 *   scan_cloud:
 *     type: process.exec
 *     timeout_ms: 60000
 *     argv:
 *     - python3
 *     - -B
 *     - '@resource{scripts/scan_cloud.py}'
 *     - --root
 *     - $root
 *     - --out
 *     - work/cloud.json
 *   scan_packages:
 *     type: process.exec
 *     timeout_ms: 120000
 *     argv:
 *     - python3
 *     - -B
 *     - '@resource{scripts/scan_packages.py}'
 *     - --root
 *     - $root
 *     - --max
 *     - $max_packages
 *     - --out
 *     - work/packages.json
 *   resolve:
 *     type: process.exec
 *     timeout_ms: 300000
 *     depends_on: [scan_runtimes, scan_containers, scan_ci, scan_cloud, scan_packages]
 *     argv:
 *     - python3
 *     - -B
 *     - '@resource{scripts/resolve.py}'
 *     - work/runtimes.json
 *     - work/containers.json
 *     - work/ci.json
 *     - work/cloud.json
 *     - work/packages.json
 *     - --out
 *     - work/facts.json
 *   join:
 *     type: process.exec
 *     timeout_ms: 60000
 *     depends_on: [resolve]
 *     argv:
 *     - python3
 *     - -B
 *     - '@resource{scripts/join.py}'
 *     - work/runtimes.json
 *     - work/containers.json
 *     - work/ci.json
 *     - work/cloud.json
 *     - work/packages.json
 *     - --facts
 *     - work/facts.json
 *     - --root
 *     - $root
 *     - --horizon
 *     - $horizon_days
 *     - --fail-on
 *     - $fail_on
 *     - --output
 *     - play
 *     - --out
 *     - work/report.json
 * ---
 */

// Seven steps in three layers. The five scanners are independent root steps
// that read only the filesystem under $root and write their findings into the
// run workspace; resolve is the one step that touches the network (public,
// keyless APIs, read-only); join turns facts into verdicts. Every script lives
// under resources/ in this package, invoked with python3 -B so that Python
// writes no bytecode into the package directory.

const presentationSdk = await import("__ROTE_PRESENTATION_SDK__").catch((cause) => {
  throw new Error(
    "This is a rote steps presentation program. Run it with `rote play run <name>`.",
    { cause },
  );
});
const { FlowOutput, loadPresentationContext, stepName } = presentationSdk;

const out = new FlowOutput();
const ctx = await loadPresentationContext();

// One entry per step. A completed step contributes its process body; anything
// else contributes its status, so a partial run still renders honestly.
type StepView = { status: string; body?: unknown; message?: string; reason?: string };

// A failed step carries its reason as output.message; some failures (a
// missing interpreter, a timeout) arrive as output.diagnostic instead. Read
// both, so the failure view never says "failed" without saying why.
function failureText(output: Record<string, unknown>): string {
  const message = output.message;
  if (typeof message === "string" && message.trim()) return message;
  const diagnostic = output.diagnostic;
  if (typeof diagnostic === "string" && diagnostic.trim()) return diagnostic;
  if (diagnostic && typeof diagnostic === "object") {
    const d = diagnostic as Record<string, unknown>;
    const parts = [d.code, d.message, d.summary, d.detail].filter((p) => typeof p === "string" && p);
    if (parts.length) return parts.join(": ");
    try { return JSON.stringify(diagnostic).slice(0, 300); } catch { /* fall through */ }
  }
  return "";
}

function view(step: ReturnType<typeof ctx.step>): StepView {
  const outcome = step.outcome as { status: string; output: Record<string, unknown> };
  switch (outcome.status) {
    case "completed":
    case "restored":
      return { status: "completed", body: outcome.output.body };
    case "skipped":
      return { status: "skipped", reason: String(outcome.output.reason ?? "") };
    case "failed":
      return { status: "failed", message: failureText(outcome.output) };
    case "blocked":
      return { status: "blocked", reason: String(outcome.output.reason ?? "") };
    default:
      return { status: outcome.status };
  }
}

// Every stepName("...") stays a literal so lint can check it against steps:.
const steps: Record<string, StepView> = {};
steps["scan_runtimes"] = view(ctx.step(stepName("scan_runtimes")));
steps["scan_containers"] = view(ctx.step(stepName("scan_containers")));
steps["scan_ci"] = view(ctx.step(stepName("scan_ci")));
steps["scan_cloud"] = view(ctx.step(stepName("scan_cloud")));
steps["scan_packages"] = view(ctx.step(stepName("scan_packages")));
steps["resolve"] = view(ctx.step(stepName("resolve")));
steps["join"] = view(ctx.step(stepName("join")));

// The report is join's stdout. Read it defensively: a process body carries
// stdout.text, and a very large report may be truncated with the full text
// kept as an artifact.
function stdoutOf(body: unknown): { text: string | null; truncated: boolean; bytes: number | null; artifact: string | null } {
  const b = body as { stdout?: { text?: unknown; bytes?: unknown; truncated?: unknown; artifact?: { path?: unknown } } } | null;
  const so = b && typeof b === "object" ? b.stdout : undefined;
  if (!so || typeof so !== "object") return { text: null, truncated: false, bytes: null, artifact: null };
  const text = typeof so.text === "string" ? so.text : null;
  const bytes = typeof so.bytes === "number" ? so.bytes : null;
  // rote keeps 65,536 bytes of a step's stdout in the preview. Trust the flag,
  // and also the byte count: a preview shorter than the process wrote is cut.
  const truncated = so.truncated === true ||
    (bytes !== null && text !== null && bytes > new TextEncoder().encode(text).length);
  return {
    text,
    truncated,
    bytes,
    artifact: typeof so.artifact?.path === "string" ? so.artifact.path : null,
  };
}

// Anything short of a clean, complete upstream step is said out loud: a
// scanner that failed or was blocked leaves a surface unscanned, and a
// preview that rote cut off is not the whole observation. join reads the
// scanners' files from the run workspace rather than their previews, so the
// report itself can still be complete; the reader is told either way.
const UPSTREAM = ["scan_runtimes", "scan_containers", "scan_ci", "scan_cloud", "scan_packages", "resolve"];
const degraded: string[] = [];
for (const name of UPSTREAM) {
  const v = steps[name];
  if (v.status === "completed") {
    const so = stdoutOf(v.body);
    if (so.truncated) {
      degraded.push(`${name}: its stdout preview was truncated` +
        (so.bytes !== null ? ` (${so.bytes} bytes written)` : "") +
        `; the full text is in ${so.artifact ?? "the run artifacts"}`);
    }
  } else if (v.status === "failed") {
    degraded.push(`${name}: failed` + (v.message ? `: ${v.message}` : ""));
  } else if (v.status === "blocked") {
    degraded.push(`${name}: blocked, did not run` + (v.reason ? ` (${v.reason})` : ""));
  } else if (v.status === "skipped") {
    degraded.push(`${name}: skipped` + (v.reason ? ` (${v.reason})` : ""));
  } else {
    degraded.push(`${name}: ${v.status}`);
  }
}

type Finding = {
  status: string; what: string; where: string; date: string | null; days: number | null;
  because: string | null; move_to: string | null; owners?: string[];
};
// join emits its bounded "play" view: every finding that is not OK, trimmed
// from the end to fit rote's stdout preview, with what was dropped declared
// and the complete report written to work/report.json in the run workspace.
type Report = {
  repo: string; generated_at: string; horizon_days: number;
  counts: Record<string, number>; distinct?: Record<string, number>;
  findings: Finding[]; ledger: { source: string; status: string; note?: string }[];
  ownership?: { source?: string | null };
  summary?: string; ok_hidden?: number; findings_omitted?: number; full_report?: string | null;
};

let report: Report | null = null;
let problem = "";
const joinView = steps["join"];
if (joinView.status === "completed") {
  const so = stdoutOf(joinView.body);
  if (so.text) {
    try {
      report = JSON.parse(so.text) as Report;
    } catch {
      problem = "the report could not be parsed" +
        (so.truncated ? `; stdout was truncated, the full text is at ${so.artifact ?? "the run artifacts"}` : "");
    }
  } else {
    problem = "the join step produced no output";
  }
} else if (joinView.status === "failed") {
  // fail_on tripped, or a real fault. join writes the reason to stderr, which
  // is what the failure message carries.
  problem = joinView.message || "the join step failed";
} else {
  problem = `the join step was ${joinView.status}` + (joinView.reason ? `: ${joinView.reason}` : "");
}

const ORDER = ["DEAD", "DYING", "WATCH", "UNKNOWN", "OK"];
const HEADLINE: Record<string, string> = {
  DEAD: "already out of support",
  DYING: "loses support inside the horizon",
  WATCH: "still supported, clock running",
  UNKNOWN: "no lifecycle data",
  OK: "supported",
};

function countPhrase(r: Report, status: string): string {
  const total = r.counts[status] ?? 0;
  const distinct = r.distinct?.[status] ?? total;
  const label = status.toLowerCase();
  return distinct && distinct < total ? `${distinct} ${label} at ${total} places` : `${total} ${label}`;
}

function whenText(f: Finding): string {
  if (!f.date) return "";
  const d = f.days ?? 0;
  const verb = d <= 0 ? "died" : "breaks";
  const rel = d < 0 ? `${-d} days ago` : d === 0 ? "today" : `in ${d} days`;
  return `  ${verb} ${f.date} (${rel})`;
}

const SHOWN_PER_STATUS = 12;
const humanLines: string[] = [];
let summaryLine = "";
let resultBody: Record<string, unknown>;

if (report) {
  const r = report;
  humanLines.push(`EOL Radar | ${r.repo} | checked ${r.generated_at} | horizon ${r.horizon_days} days`);
  humanLines.push("=".repeat(Math.min(humanLines[0].length, 78)));
  humanLines.push("");
  const bits = ORDER.filter((s) => (r.counts[s] ?? 0) > 0).map((s) => countPhrase(r, s));
  humanLines.push("  " + (bits.length ? bits.join(" | ") : "nothing found to check"));
  humanLines.push("");

  if (degraded.length > 0) {
    humanLines.push(`INCOMPLETE - ${degraded.length} step(s) did not complete cleanly; treat the findings as partial`);
    humanLines.push("-".repeat(78));
    for (const line of degraded) humanLines.push(`  ${line.slice(0, 200)}`);
    humanLines.push("");
  }

  const shown = r.findings.filter((f) => f.status !== "OK");
  if (shown.length === 0) {
    humanLines.push("  Nothing in this repository is out of support or expiring inside the horizon.");
    humanLines.push("");
  }
  let hidden = 0;
  for (const status of ["DEAD", "DYING", "WATCH", "UNKNOWN"]) {
    const group = shown.filter((f) => f.status === status);
    if (group.length === 0) continue;
    humanLines.push(`${status} - ${HEADLINE[status]} (${group.length})`);
    humanLines.push("-".repeat(78));
    // Say each distinct fact once and list where it lives.
    const seen = new Map<string, { f: Finding; places: string[] }>();
    for (const f of group) {
      const key = `${f.what}|${f.because ?? ""}|${f.date ?? ""}`;
      const entry = seen.get(key);
      if (entry) entry.places.push(f.where);
      else seen.set(key, { f, places: [f.where] });
    }
    let printed = 0;
    for (const { f, places } of seen.values()) {
      if (printed >= SHOWN_PER_STATUS) { hidden += 1; continue; }
      humanLines.push(`  ${f.what}`);
      humanLines.push(`    ${places[0]}${whenText(f)}`);
      if (places.length > 1) humanLines.push(`    also at ${places.length - 1} other place(s)`);
      if (f.because) humanLines.push(`    why: ${f.because}`);
      if (f.move_to) humanLines.push(`    fix: ${f.move_to}`);
      if (f.owners && f.owners.length) humanLines.push(`    owner: ${f.owners.join(", ")}`);
      humanLines.push("");
      printed += 1;
    }
  }
  if (hidden > 0) {
    humanLines.push(`  ${hidden} further distinct finding(s) not shown here; every one is in --output=json.`);
    humanLines.push("");
  }
  if ((r.findings_omitted ?? 0) > 0) {
    humanLines.push(`  ${r.findings_omitted} lower-severity finding(s) were left out of this view to fit; ` +
      `the complete report is at ${r.full_report ?? "work/report.json"} in the run workspace.`);
    humanLines.push("");
  }
  humanLines.push("LEDGER");
  humanLines.push("-".repeat(78));
  for (const row of r.ledger) {
    humanLines.push(`  ${row.status.padEnd(12)}${row.source.padEnd(28)}${row.note ?? ""}`);
  }
  humanLines.push("");
  humanLines.push("Lifecycle dates from endoflife.date; package status from deps.dev and npm;");
  humanLines.push("action runtimes read from each action.yml at the pinned ref.");
  const okHidden = r.ok_hidden ?? r.counts["OK"] ?? 0;
  if (okHidden > 0) {
    humanLines.push(`${okHidden} supported item(s) not listed above; all of them are in ${r.full_report ?? "the full report"}.`);
  }

  const soonest = r.findings.find((f) => (f.status === "DEAD" || f.status === "DYING") && f.date);
  summaryLine = r.summary ??
    (`EOL Radar: ${countPhrase(r, "DEAD")} | ${countPhrase(r, "DYING")} <=${r.horizon_days}d | ` +
    `${countPhrase(r, "WATCH")} | ${r.counts["OK"] ?? 0} ok` +
    ((r.counts["UNKNOWN"] ?? 0) > 0 ? ` | ${r.counts["UNKNOWN"]} unknown` : "") +
    ` | repo=${r.repo} | ${r.generated_at}` +
    (soonest ? ` | next: ${soonest.what} ${soonest.date}` : ""));
  if (degraded.length > 0) {
    summaryLine += ` · incomplete: ${degraded.map((d) => d.split(":")[0]).join(", ")}`;
  }

  resultBody = {
    run_id: ctx.run.run_id,
    report: r,
    degraded,
    steps: Object.fromEntries(Object.entries(steps).map(([k, v]) => [k, v.status])),
  };
} else {
  humanLines.push("EOL Radar could not produce a report.");
  humanLines.push(`  ${problem}`);
  humanLines.push("");
  humanLines.push("STEPS");
  humanLines.push("-".repeat(78));
  for (const [name, v] of Object.entries(steps)) {
    const detail = v.message ?? v.reason ?? "";
    humanLines.push(`  ${v.status.padEnd(11)}${name.padEnd(18)}${detail.slice(0, 160)}`);
  }
  summaryLine = `EOL Radar: no report; ${problem.slice(0, 140)}`;
  resultBody = {
    run_id: ctx.run.run_id,
    error: problem,
    degraded,
    steps: Object.fromEntries(Object.entries(steps).map(([k, v]) => [k, { status: v.status, message: v.message ?? v.reason ?? null }])),
  };
}

out.human(humanLines.join("\n"));
out.summary(summaryLine);
out.result(resultBody);
