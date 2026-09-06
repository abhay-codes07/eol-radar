#!/usr/bin/env -S rote play run
/**
 * @rote-frontmatter
 * ---
 * name: eol-radar
 * description: "One job: what in this repository stops working, and on what date. Reads runtime pins, base images, CI runners and actions (resolved to the Node runtime their action.yml really declares, so SHA pins are caught), cloud runtimes and packages, then prints everything with a death date, soonest first, with the exact version to move to. Outdated is not the same as dead: this is about dates a platform enforces, not the newest release. Read-only, no credentials; the whole scanner ships inside this package and nothing is fetched at run time. root must be an absolute path: a step runs inside rote's workspace, not your shell."
 * source: https://github.com/abhay-codes07/eol-radar
 * tags:
 * - end-of-life
 * - eol
 * - deprecation
 * - lifecycle
 * - github-actions
 * - node20
 * - docker
 * - lambda
 * - dependencies
 * - effect-read-only
 * - domain-devops
 * - job-lifecycle-audit
 * provenance:
 *   author: abhay-codes07 <abhaysingh0293@gmail.com>
 * parameters:
 *   - name: root
 *     type: string
 *     required: true
 *     description: 'Absolute path of the repository to scan, for example /home/you/project. A relative path such as . is refused: a step runs inside the rote workspace, not in your shell, so it would scan the wrong directory.'
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
 *     description: 'none, dying or dead. The gate step fails the run when a finding of that severity exists, for use as a CI gate; the report is rendered either way.'
 * output:
 *   format: json
 *   schema:
 *     type: object
 *     properties:
 *       run_id:
 *         type: string
 *       report:
 *         type: object
 *         description: the bounded play view of the report, with counts, distinct, findings, ledger, summary and the path of the complete report
 *       gate:
 *         type: object
 *         description: status, fail_on and tripped for the CI gate step
 *       degraded:
 *         type: array
 *         description: one line per upstream step that failed, was blocked or was skipped
 *       notes:
 *         type: array
 *         description: one line per step whose stdout preview rote cut, with what was read from disk instead
 *       steps:
 *         type: object
 * metadata:
 *   version: 0.1.8
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
 *     - effect-read-only
 *   hardcode_audit:
 *     schema: 2
 *     suspicion_count: 3
 *     audit_sha256: 5a608495752f3dd56e5ab94193579a75d6a75430b7cd2adf60b49e6bcdb3ffec
 * presentation_fixtures:
 *   scan_runtimes: resources/presentation-fixtures/scan_runtimes/fixture.yaml
 *   scan_containers: resources/presentation-fixtures/scan_containers/fixture.yaml
 *   scan_ci: resources/presentation-fixtures/scan_ci/fixture.yaml
 *   scan_cloud: resources/presentation-fixtures/scan_cloud/fixture.yaml
 *   scan_packages: resources/presentation-fixtures/scan_packages/fixture.yaml
 *   resolve: resources/presentation-fixtures/resolve/fixture.yaml
 *   join: resources/presentation-fixtures/join/fixture.yaml
 *   gate: resources/presentation-fixtures/gate/fixture.yaml
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
 *     - --in-play
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
 *     - --in-play
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
 *     - --in-play
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
 *     - --in-play
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
 *     - --in-play
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
 *     - --cache
 *     - work/cache
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
 *     - --output
 *     - play
 *     - --out
 *     - work/report.json
 *   gate:
 *     type: process.exec
 *     timeout_ms: 30000
 *     depends_on: [join]
 *     argv:
 *     - python3
 *     - -B
 *     - '@resource{scripts/gate.py}'
 *     - --report
 *     - work/report.json
 *     - --fail-on
 *     - $fail_on
 * ---
 */

// Eight steps in four layers. The five scanners are independent root steps
// that read only the filesystem under $root and write their findings into the
// run workspace; resolve is the one step that touches the network (public,
// keyless APIs, read-only, cached inside the workspace); join turns facts into
// verdicts; gate is the CI gate, its own step so that a tripped gate fails the
// run without hiding the report. Every script lives under resources/ in this
// package, invoked with python3 -B so that Python writes no bytecode into the
// package directory.

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

// A failed step carries its reason as output.message and the runner's own
// diagnosis as output.diagnostic (exit kind, timeout, stderr). A timeout has
// to read as a timeout, never as a bad input.
function failureText(output: Record<string, unknown>): string {
  let diag = "";
  const diagnostic = output.diagnostic;
  if (diagnostic && typeof diagnostic === "object") {
    const d = diagnostic as Record<string, unknown>;
    const exit = d.exit && typeof d.exit === "object" ? d.exit as Record<string, unknown> : null;
    if (exit?.kind === "timed_out") {
      diag = `timed out after ${exit.timeout_ms ?? d.timeout_ms ?? "the step's"} ms`;
    } else if (exit?.kind === "signal") {
      diag = `killed by signal ${exit.signal ?? ""}`.trim();
    }
    const stderr = typeof d.stderr === "string" ? d.stderr.trim() : "";
    if (stderr) diag = diag ? `${diag}; stderr: ${stderr}` : stderr;
    if (!diag) {
      const parts = [d.code, d.message, d.summary, d.detail].filter((p) => typeof p === "string" && p);
      if (parts.length) diag = parts.join(": ");
    }
  } else if (typeof diagnostic === "string") {
    diag = diagnostic.trim();
  }
  const message = typeof output.message === "string" ? output.message.trim() : "";
  if (diag.startsWith("timed out")) return message ? `${diag} (${message})` : diag;
  return message || diag;
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
steps["gate"] = view(ctx.step(stepName("gate")));

// A process body carries stdout.text; rote keeps 65,536 bytes of it and sets
// truncated when the process wrote more, with the whole text in an artifact.
// Trust the flag, and also the byte count: a preview shorter than the process
// wrote is cut whatever the flag says.
function stdoutOf(body: unknown): { text: string | null; truncated: boolean; bytes: number | null; artifact: string | null } {
  const b = body as { stdout?: { text?: unknown; bytes?: unknown; truncated?: unknown; artifact?: { path?: unknown } } } | null;
  const so = b && typeof b === "object" ? b.stdout : undefined;
  if (!so || typeof so !== "object") return { text: null, truncated: false, bytes: null, artifact: null };
  const text = typeof so.text === "string" ? so.text : null;
  const bytes = typeof so.bytes === "number" ? so.bytes : null;
  const truncated = so.truncated === true ||
    (bytes !== null && text !== null && bytes > new TextEncoder().encode(text).length);
  return {
    text,
    truncated,
    bytes,
    artifact: typeof so.artifact?.path === "string" ? so.artifact.path : null,
  };
}

// Two different things can be wrong upstream, and they are kept apart. A
// scanner that failed, was blocked or was skipped left a surface unscanned:
// that makes the findings partial, and the report says INCOMPLETE. A preview
// that rote cut is not data loss here: every step reads the previous step's
// file from work/, never its preview, so the report is complete and the
// reader is told where the whole text went.
const degraded: string[] = [];
const notes: string[] = [];
for (const name of Object.keys(steps)) {
  if (name === "join") continue;
  const v = steps[name];
  if (v.status === "completed") {
    const so = stdoutOf(v.body);
    if (so.truncated) {
      notes.push(`${name}: its stdout preview was truncated` +
        (so.bytes !== null ? ` (${so.bytes} bytes written, 65536 kept)` : "") +
        `; the next step reads ${name}'s file in work/, not the preview, so nothing was lost;` +
        ` the full text is in ${so.artifact ?? "the run artifacts"}`);
    }
    continue;
  }
  if (name === "gate") continue;   // the gate's failure is its verdict, rendered below
  if (v.status === "failed") {
    degraded.push(`${name}: failed` + (v.message ? `: ${v.message}` : ""));
  } else if (v.status === "blocked") {
    degraded.push(`${name}: blocked, did not run` + (v.reason ? ` (${v.reason})` : ""));
  } else if (v.status === "skipped") {
    degraded.push(`${name}: skipped` + (v.reason ? ` (${v.reason})` : ""));
  } else {
    degraded.push(`${name}: ${v.status}`);
  }
}

// The gate. Completed means it passed and printed its verdict; failed with
// "fail_on=<x> tripped" on stderr means it did its job and the run fails on
// purpose; any other failure is a fault and is named as one.
type Gate = { status: string; fail_on: string | null; tripped: boolean | null; text: string };
function readGate(v: StepView): Gate {
  if (v.status === "completed") {
    const so = stdoutOf(v.body);
    let parsed: Record<string, unknown> | null = null;
    try { parsed = so.text ? JSON.parse(so.text) as Record<string, unknown> : null; } catch { parsed = null; }
    if (parsed && typeof parsed === "object") {
      const failOn = String(parsed.fail_on ?? "none");
      const tripped = parsed.tripped === true;
      return tripped
        ? { status: "tripped", fail_on: failOn, tripped: true, text: `GATE fail_on=${failOn} tripped` }
        : { status: "passed", fail_on: failOn, tripped: false, text: `gate passed (fail_on=${failOn})` };
    }
    return { status: "unreadable", fail_on: null, tripped: null,
      text: "the gate's verdict could not be read" + (so.truncated ? " (its preview was truncated)" : "") };
  }
  if (v.status === "failed") {
    const m = v.message ?? "";
    const match = m.match(/fail_on=(\w+) tripped/);
    if (match) {
      return { status: "tripped", fail_on: match[1], tripped: true,
        text: `GATE TRIPPED: fail_on=${match[1]}. The run fails on purpose; the report above is complete.` };
    }
    return { status: "failed", fail_on: null, tripped: null, text: `gate failed: ${m || "no reason given"}` };
  }
  if (v.status === "blocked") {
    return { status: "blocked", fail_on: null, tripped: null,
      text: `gate blocked, did not run` + (v.reason ? ` (${v.reason})` : "") };
  }
  return { status: v.status, fail_on: null, tripped: null,
    text: `gate ${v.status}` + (v.reason ? ` (${v.reason})` : "") };
}
const gate = readGate(steps["gate"]);

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
  unreadable?: Record<string, string[]>;
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
      problem = so.truncated
        ? `the report could not be parsed because its stdout preview was truncated` +
          (so.bytes !== null ? ` (${so.bytes} bytes written, 65536 kept)` : "") +
          `; the full text is at ${so.artifact ?? "the run artifacts"} and the complete report at work/report.json`
        : "the report could not be parsed";
    }
  } else {
    problem = "the join step produced no output";
  }
} else if (joinView.status === "failed") {
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

function stepNames(lines: string[]): string {
  return lines.map((d) => d.split(":")[0]).join(", ");
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
  const observed = ORDER.some((s) => (r.counts[s] ?? 0) > 0);
  if (observed) {
    humanLines.push("  " + ORDER.filter((s) => (r.counts[s] ?? 0) > 0).map((s) => countPhrase(r, s)).join(" | "));
  } else {
    // An empty scan is a non-observation, not a clean one, and the most
    // likely cause is a root that does not point at the repository.
    humanLines.push("  Nothing to check: no runtime pins, base images, workflows, deployment");
    humanLines.push("  manifests or package manifests were found under this root.");
    humanLines.push("  That is a non-observation, not a clean bill of health. Check that root");
    humanLines.push("  points at the repository, as an absolute path.");
  }
  humanLines.push("");

  // A directory a scanner could not open makes the scan partial. join names
  // them; they are repeated here so the reader sees it before the findings.
  const byDirectory = new Map<string, string[]>();
  for (const [surface, dirs] of Object.entries(r.unreadable ?? {})) {
    for (const d of dirs ?? []) byDirectory.set(d, [...(byDirectory.get(d) ?? []), surface]);
  }
  const unreadable = [...byDirectory.entries()].sort().map(([d, s]) => `${d} (${s.join(", ")})`);
  if (unreadable.length > 0) {
    humanLines.push(`PARTIAL - ${unreadable.length} director${unreadable.length === 1 ? "y" : "ies"} could not be read; this scan is incomplete`);
    humanLines.push("-".repeat(78));
    for (const d of unreadable.slice(0, 10)) humanLines.push(`  ${d}`);
    if (unreadable.length > 10) humanLines.push(`  and ${unreadable.length - 10} more`);
    humanLines.push("");
  }

  if (gate.status !== "passed") {
    humanLines.push(`GATE - ${gate.text}`);
    humanLines.push("");
  }
  if (degraded.length > 0) {
    humanLines.push(`INCOMPLETE - ${degraded.length} step(s) did not complete; a surface went unscanned, treat the findings as partial`);
    humanLines.push("-".repeat(78));
    for (const line of degraded) humanLines.push(`  ${line.slice(0, 220)}`);
    humanLines.push("");
  }

  const shown = r.findings.filter((f) => f.status !== "OK");
  if (shown.length === 0 && observed) {
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
  if (notes.length > 0) {
    humanLines.push("");
    humanLines.push("NOTES");
    humanLines.push("-".repeat(78));
    for (const line of notes) humanLines.push(`  ${line.slice(0, 260)}`);
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
  if (gate.status === "tripped") summaryLine += ` · gate tripped (fail_on=${gate.fail_on})`;
  else if (gate.status !== "passed") summaryLine += ` · gate ${gate.status}`;
  if (degraded.length > 0) summaryLine += ` · incomplete: ${stepNames(degraded)}`;
  if (notes.length > 0) summaryLine += ` · preview truncated: ${stepNames(notes)}`;

  resultBody = {
    run_id: ctx.run.run_id,
    report: r,
    gate: { status: gate.status, fail_on: gate.fail_on, tripped: gate.tripped },
    degraded,
    notes,
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
    humanLines.push(`  ${v.status.padEnd(11)}${name.padEnd(18)}${detail.slice(0, 200)}`);
  }
  if (notes.length > 0) {
    humanLines.push("");
    for (const line of notes) humanLines.push(`  ${line.slice(0, 260)}`);
  }
  summaryLine = `EOL Radar: no report; ${problem.slice(0, 160)}`;
  if (degraded.length > 0) summaryLine += ` · incomplete: ${stepNames(degraded)}`;
  resultBody = {
    run_id: ctx.run.run_id,
    error: problem,
    gate: { status: gate.status, fail_on: gate.fail_on, tripped: gate.tripped },
    degraded,
    notes,
    steps: Object.fromEntries(Object.entries(steps).map(([k, v]) => [k, { status: v.status, message: v.message ?? v.reason ?? null }])),
  };
}

out.human(humanLines.join("\n"));
out.summary(summaryLine);
out.result(resultBody);
