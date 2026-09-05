#!/usr/bin/env -S rote play run
/**
 * @rote-frontmatter
 * ---
 * name: eol-radar
 * description: "One job: what in this repository stops working, and on what date. Reads runtime pins, base images, CI runners and actions (resolved to the Node runtime their action.yml really declares, so SHA pins are caught), cloud runtimes and packages, then prints everything with a death date, soonest first, with the exact version to move to. Outdated is not the same as dead: this is about dates a platform enforces, not the newest release. Read-only, no credentials."
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
 *   rote_version: "0.80.0"
 *   status: draft
 *   kind: atomic
 *   flow_type: parallel
 *   execution_model: steps_with_presentation
 *   requires_sessions: false
 *   hardcode_audit:
 *     schema: 2
 *     suspicion_count: 3
 *     audit_sha256: 285720fc60556e694529acb26547eff80dc5702931b0e37e2fb0d97f3b6032a5
 * steps:
 *   fetch_tool:
 *     type: process.exec
 *     timeout_ms: 120000
 *     argv:
 *     - git
 *     - clone
 *     - --depth
 *     - '1'
 *     - --branch
 *     - v0.1.1
 *     - https://github.com/abhay-codes07/eol-radar.git
 *     - eol-radar-tool
 *   scan_runtimes:
 *     type: process.exec
 *     timeout_ms: 60000
 *     depends_on: [fetch_tool]
 *     argv:
 *     - python3
 *     - eol-radar-tool/scripts/scan_runtimes.py
 *     - --root
 *     - $root
 *     - --out
 *     - work/runtimes.json
 *   scan_containers:
 *     type: process.exec
 *     timeout_ms: 60000
 *     depends_on: [fetch_tool]
 *     argv:
 *     - python3
 *     - eol-radar-tool/scripts/scan_containers.py
 *     - --root
 *     - $root
 *     - --out
 *     - work/containers.json
 *   scan_ci:
 *     type: process.exec
 *     timeout_ms: 60000
 *     depends_on: [fetch_tool]
 *     argv:
 *     - python3
 *     - eol-radar-tool/scripts/scan_ci.py
 *     - --root
 *     - $root
 *     - --out
 *     - work/ci.json
 *   scan_cloud:
 *     type: process.exec
 *     timeout_ms: 60000
 *     depends_on: [fetch_tool]
 *     argv:
 *     - python3
 *     - eol-radar-tool/scripts/scan_cloud.py
 *     - --root
 *     - $root
 *     - --out
 *     - work/cloud.json
 *   scan_packages:
 *     type: process.exec
 *     timeout_ms: 120000
 *     depends_on: [fetch_tool]
 *     argv:
 *     - python3
 *     - eol-radar-tool/scripts/scan_packages.py
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
 *     - eol-radar-tool/scripts/resolve.py
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
 *     - eol-radar-tool/scripts/join.py
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
 *     - json
 * ---
 */

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

function view(step: ReturnType<typeof ctx.step>): StepView {
  const outcome = step.outcome as { status: string; output: Record<string, unknown> };
  switch (outcome.status) {
    case "completed":
    case "restored":
      return { status: "completed", body: outcome.output.body };
    case "skipped":
      return { status: "skipped", reason: String(outcome.output.reason ?? "") };
    case "failed":
      return { status: "failed", message: String(outcome.output.message ?? "") };
    case "blocked":
      return { status: "blocked", reason: String(outcome.output.reason ?? "") };
    default:
      return { status: outcome.status };
  }
}

// Every stepName("...") stays a literal so lint can check it against steps:.
const steps: Record<string, StepView> = {};
steps["fetch_tool"] = view(ctx.step(stepName("fetch_tool")));
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
function stdoutOf(body: unknown): { text: string | null; truncated: boolean; artifact: string | null } {
  const b = body as { stdout?: { text?: unknown; truncated?: unknown; artifact?: { path?: unknown } } } | null;
  const so = b && typeof b === "object" ? b.stdout : undefined;
  if (!so || typeof so !== "object") return { text: null, truncated: false, artifact: null };
  return {
    text: typeof so.text === "string" ? so.text : null,
    truncated: so.truncated === true,
    artifact: typeof so.artifact?.path === "string" ? so.artifact.path : null,
  };
}

type Finding = {
  status: string; what: string; where: string; date: string | null; days: number | null;
  because: string | null; move_to: string | null; owners?: string[];
};
type Report = {
  repo: string; generated_at: string; horizon_days: number;
  counts: Record<string, number>; distinct?: Record<string, number>;
  findings: Finding[]; ledger: { source: string; status: string; note?: string }[];
  ownership?: { source?: string | null };
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
        (so.truncated ? `; stdout was truncated, the full text is at ${so.artifact ?? "the run's artifacts"}` : "");
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
  humanLines.push("LEDGER");
  humanLines.push("-".repeat(78));
  for (const row of r.ledger) {
    humanLines.push(`  ${row.status.padEnd(12)}${row.source.padEnd(28)}${row.note ?? ""}`);
  }
  humanLines.push("");
  humanLines.push("Lifecycle dates from endoflife.date; package status from deps.dev and npm;");
  humanLines.push("action runtimes read from each action.yml at the pinned ref.");
  if ((r.counts["OK"] ?? 0) > 0) {
    humanLines.push(`${r.counts["OK"]} supported item(s) not listed above; all of them are in --output=json.`);
  }

  const soonest = r.findings.find((f) => (f.status === "DEAD" || f.status === "DYING") && f.date);
  summaryLine = `EOL Radar: ${countPhrase(r, "DEAD")} | ${countPhrase(r, "DYING")} <=${r.horizon_days}d | ` +
    `${countPhrase(r, "WATCH")} | ${r.counts["OK"] ?? 0} ok` +
    ((r.counts["UNKNOWN"] ?? 0) > 0 ? ` | ${r.counts["UNKNOWN"]} unknown` : "") +
    ` | repo=${r.repo} | ${r.generated_at}` +
    (soonest ? ` | next: ${soonest.what} ${soonest.date}` : "");

  resultBody = {
    run_id: ctx.run.run_id,
    report: r,
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
    humanLines.push(`  ${v.status.padEnd(11)}${name.padEnd(18)}${detail.slice(0, 120)}`);
  }
  summaryLine = `EOL Radar: no report; ${problem.slice(0, 140)}`;
  resultBody = {
    run_id: ctx.run.run_id,
    error: problem,
    steps: Object.fromEntries(Object.entries(steps).map(([k, v]) => [k, { status: v.status, message: v.message ?? v.reason ?? null }])),
  };
}

out.human(humanLines.join("\n"));
out.summary(summaryLine);
out.result(resultBody);
