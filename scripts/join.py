"""Join what is in the repository with what is known about its lifecycle, and
say what breaks first.

Reads the surface files and the resolver's facts, applies the curated
enforcement table, then renders the three views rote asks for: human, summary
and canonical JSON. No fact present in the JSON is absent from the human view.
"""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as c
import eol_data as ed

STATUS_RANK = {"DEAD": 0, "DYING": 1, "WATCH": 2, "UNKNOWN": 3, "OK": 4}
STATUS_ORDER = ["DEAD", "DYING", "WATCH", "UNKNOWN", "OK"]


def _console_is_unicode():
    """Only decorate the output when the stream is genuinely UTF.

    Windows legacy codepages can encode a middle dot but terminals that expect
    UTF-8 then render it as a replacement glyph, so 'it encodes' is not a good
    enough test. ASCII separators everywhere else keep the report readable.
    """
    encoding = (getattr(sys.stdout, "encoding", None) or "ascii").lower()
    return encoding.replace("-", "").replace("_", "").startswith("utf")


UNICODE_OK = _console_is_unicode()
DOT = u" · " if UNICODE_OK else " | "
DASH = u" — " if UNICODE_OK else " - "

HEADLINES = {
    "DEAD": "already out of support",
    "DYING": "loses support inside the horizon",
    "WATCH": "still supported, clock running",
    "UNKNOWN": "no lifecycle data",
    "OK": "supported",
}


def parse_date(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.date(*[int(part) for part in value[:10].split("-")])
    except (ValueError, TypeError):
        return None


def days_between(target, today):
    if not target:
        return None
    return (target - today).days


def human_days(days):
    if days is None:
        return ""
    if days < 0:
        magnitude = -days
        return str(magnitude) + (" day ago" if magnitude == 1 else " days ago")
    if days == 0:
        return "today"
    return "in " + str(days) + (" day" if days == 1 else " days")


# ---------------------------------------------------------------------------
# Enforcement overlay
# ---------------------------------------------------------------------------

def load_enforcement(path):
    data = c.read_json(path)
    if not isinstance(data, dict):
        return {"verified": None, "rules": []}
    return data


def rule_matches(rule, context):
    match = rule.get("match") or {}
    if match.get("kind") and match["kind"] != context.get("kind"):
        return False
    for field in ("platform", "product"):
        if match.get(field) and match[field] != context.get(field):
            return False
    if match.get("using") and context.get("using") not in match["using"]:
        return False
    if match.get("cycle") and context.get("cycle") not in match["cycle"]:
        return False
    return True


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

def evaluate(subject, facts, rules, today, horizon):
    lookup = subject.get("lookup") or {}
    kind = subject.get("kind")
    finding = {
        "status": "UNKNOWN",
        "kind": kind,
        "what": subject.get("label"),
        "where": subject.get("where"),
        "raw": subject.get("raw"),
        "date": None,
        "date_kind": None,
        "days": None,
        "because": None,
        "move_to": subject.get("fix"),
        "notes": [],
        "sources": [],
    }
    if subject.get("note"):
        finding["notes"].append(subject["note"])

    context = {
        "kind": kind,
        "platform": subject.get("platform"),
        "product": lookup.get("product"),
        "cycle": subject.get("cycle"),
        "using": subject.get("using"),
    }

    kind_of_lookup = lookup.get("type")
    if kind_of_lookup == "eol":
        _evaluate_eol(finding, subject, lookup, facts, context)
    elif kind_of_lookup == "action":
        _evaluate_action(finding, subject, lookup, facts, context)
    elif kind_of_lookup == "package":
        _evaluate_package(finding, subject, lookup, facts)
    else:
        reason = lookup.get("reason") or "no lifecycle source"
        if subject.get("using"):
            # An action read straight off disk: judge it the same way as one
            # fetched from a ref, so a local action already on node24 is OK
            # rather than unknown.
            finding["because"] = "declares runs.using: " + subject["using"]
            if subject["using"] in ed.LIVE_ACTION_RUNTIMES:
                finding["status"] = "OK"
        elif reason.startswith("floating label"):
            finding["status"] = "OK"
            finding["because"] = reason
        else:
            finding["because"] = reason

    # The curated platform table can move the operative date earlier. Whether or
    # not it wins the date, the rule is always reported: a deploy that stops
    # working on a fixed day is the actionable fact even when the release line
    # died earlier for its own reasons.
    for rule in rules:
        if not rule_matches(rule, context):
            continue
        rule_date = parse_date(rule.get("date"))
        if not rule_date:
            continue
        finding["sources"].append(rule.get("source"))
        finding["notes"].append(rule.get("detail"))
        current = finding["date"]
        if current is None or rule_date < current:
            if current is not None and finding.get("because"):
                finding["notes"].append("its release line " + finding["because"].split(" end of life ")[0]
                                        + " is listed end of life " + current.isoformat()
                                        if " end of life " in finding["because"] else finding["because"])
            finding["date"] = rule_date
            finding["date_kind"] = "enforcement"
            finding["because"] = rule["headline"] + " on " + rule_date.isoformat()
        else:
            finding["notes"].append(rule["headline"] + " on " + rule_date.isoformat())
        if rule.get("fix"):
            finding["move_to"] = rule["fix"]

    _finalize(finding, today, horizon)
    return finding


def _evaluate_eol(finding, subject, lookup, facts, context):
    product = lookup.get("product")
    fact = facts.get("eol:" + str(product))
    finding["sources"].append("https://endoflife.date/" + str(product))
    if not isinstance(fact, dict) or not fact.get("known"):
        reason = (fact or {}).get("reason") or "no data for " + str(product)
        finding["because"] = reason
        if (fact or {}).get("degraded"):
            finding["notes"].append("lifecycle source was unreachable on this run")
        return
    cycles = fact.get("cycles") or {}
    cycle = ed.match_cycle(lookup.get("cycle"), list(cycles.keys()))
    if not cycle:
        finding["because"] = ("version " + str(lookup.get("cycle"))
                              + " does not match a tracked " + str(product) + " release line")
        return
    context["cycle"] = cycle
    release = cycles[cycle]
    eol_date = parse_date(release.get("eol"))
    eoas_date = parse_date(release.get("eoas"))
    eoes_date = parse_date(release.get("eoes"))
    finding["cycle"] = cycle
    finding["date"] = eol_date
    finding["date_kind"] = "eol"
    label = str(product) + " " + str(cycle)
    if eol_date:
        finding["because"] = label + " end of life " + eol_date.isoformat()
    elif release.get("is_eol"):
        finding["because"] = label + " is marked end of life"
    else:
        finding["because"] = label + " has no published end-of-life date"
    if eoas_date:
        finding["eoas"] = eoas_date.isoformat()
    if eoes_date:
        finding["notes"].append("extended security maintenance until " + eoes_date.isoformat())
    if release.get("latest"):
        finding["notes"].append("latest in this line: " + str(release["latest"]))
    target = fact.get("newest_lts") or fact.get("newest_maintained")
    if target and target != cycle and not finding["move_to"]:
        suffix = " (current LTS)" if fact.get("newest_lts") == target else ""
        finding["move_to"] = "move to " + str(product) + " " + str(target) + suffix
    finding["_is_eol_flag"] = bool(release.get("is_eol"))
    finding["_eoas"] = eoas_date


def _evaluate_action(finding, subject, lookup, facts, context):
    key = "action:%s/%s|%s@%s" % (lookup.get("owner"), lookup.get("repo"),
                                  lookup.get("path") or "", lookup.get("ref"))
    fact = facts.get(key)
    finding["sources"].append("https://github.com/%s/%s" % (lookup.get("owner"), lookup.get("repo")))
    if not isinstance(fact, dict) or not fact.get("known"):
        finding["because"] = (fact or {}).get("reason") or "could not read action.yml"
        if (fact or {}).get("degraded"):
            finding["notes"].append("action.yml was unreachable on this run")
        return
    using = fact.get("using")
    context["using"] = using
    finding["using"] = using
    finding["because"] = "declares runs.using: " + str(using)
    finding["notes"].append("read from " + str(fact.get("source")))
    if using in ed.LIVE_ACTION_RUNTIMES:
        finding["status"] = "OK"


def _evaluate_package(finding, subject, lookup, facts):
    key = "pkg:%s:%s:%s" % (lookup.get("system"), lookup.get("name"), lookup.get("version"))
    fact = facts.get(key)
    finding["sources"].append("https://deps.dev/%s/%s" % (lookup.get("system"), lookup.get("name")))
    if not isinstance(fact, dict):
        finding["because"] = "no package data"
        return
    upstream = fact.get("upstream") or {}
    if fact.get("deprecated"):
        finding["status"] = "DEAD"
        finding["date_kind"] = "deprecated"
        reason = (fact.get("reason") or "").strip()
        finding["because"] = "deprecated by its publisher" + (": " + reason if reason else "")
        finding["move_to"] = finding["move_to"] or "replace this dependency"
    elif upstream.get("known") and upstream.get("archived"):
        finding["status"] = "DEAD"
        finding["date_kind"] = "archived"
        finding["because"] = "upstream repository is archived"
        finding["move_to"] = finding["move_to"] or "replace this dependency"
    elif fact.get("known"):
        finding["status"] = "OK"
        finding["because"] = "published and not deprecated"
    else:
        finding["because"] = fact.get("reason") or "not found upstream"
        if fact.get("degraded"):
            finding["notes"].append("package source was unreachable on this run")
    if upstream.get("known") and upstream.get("pushed_at"):
        finding["notes"].append("upstream last pushed " + str(upstream["pushed_at"])[:10])
    if fact.get("advisories"):
        finding["notes"].append(str(fact["advisories"]) + " security advisory(ies) on this version")


def _finalize(finding, today, horizon):
    date = finding.get("date")
    days = days_between(date, today) if isinstance(date, datetime.date) else None
    finding["days"] = days
    if finding["status"] == "DEAD":
        pass
    elif date is not None:
        if days is not None and days <= 0:
            finding["status"] = "DEAD"
        elif days is not None and days <= horizon:
            finding["status"] = "DYING"
        elif days is not None and days <= 365:
            finding["status"] = "WATCH"
        else:
            finding["status"] = "OK"
    elif finding.get("_is_eol_flag"):
        finding["status"] = "DEAD"
    elif finding["status"] not in ("OK", "UNKNOWN"):
        finding["status"] = "UNKNOWN"

    eoas = finding.get("_eoas")
    if finding["status"] == "OK" and isinstance(eoas, datetime.date) and eoas <= today:
        finding["status"] = "WATCH"
        finding["notes"].append("active support ended " + eoas.isoformat() + "; security fixes only")

    finding["date"] = date.isoformat() if isinstance(date, datetime.date) else None
    finding.pop("_is_eol_flag", None)
    finding.pop("_eoas", None)
    finding["notes"] = [n for n in finding["notes"] if n]
    finding["sources"] = sorted(set(s for s in finding["sources"] if s))


def sort_key(finding):
    return (
        STATUS_RANK.get(finding["status"], 9),
        finding["date"] or "9999-99-99",
        finding.get("kind") or "",
        finding.get("what") or "",
    )


def finding_key(finding):
    """Identity for the run-to-run diff.

    The line number is deliberately excluded: editing a file above a pin shifts
    every line below it, and a shifted line is not a new finding.
    """
    where = str(finding.get("where") or "")
    head, sep, tail = where.rpartition(":")
    if sep and tail.isdigit():
        where = head
    return "|".join([str(finding.get("kind")), str(finding.get("what")), where])


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def render_human(report):
    lines = []
    counts = report["counts"]
    head = ("EOL Radar" + DOT + report["repo"] + DOT + "checked " + report["generated_at"]
            + DOT + "horizon " + str(report["horizon_days"]) + " days")
    lines.append(head)
    lines.append("=" * min(len(head), 78))
    lines.append("")

    summary_bits = [str(counts[status]) + " " + status.lower() for status in STATUS_ORDER if counts[status]]
    lines.append("  " + (DOT.strip().join([" " + b + " " for b in summary_bits]).strip()
                          if summary_bits else "nothing found to check"))
    lines.append("")

    shown = [f for f in report["findings"] if f["status"] != "OK"]
    if not shown:
        lines.append("  Nothing in this repository is out of support or expiring inside the horizon.")
        lines.append("")
    for status in STATUS_ORDER:
        group = [f for f in shown if f["status"] == status]
        if not group:
            continue
        lines.append(status + DASH + HEADLINES[status] + " (" + str(len(group)) + ")")
        lines.append("-" * 78)
        for finding in group:
            when = ""
            if finding["date"]:
                verb = "died" if (finding["days"] is not None and finding["days"] <= 0) else "breaks"
                when = "  " + verb + " " + finding["date"] + " (" + human_days(finding["days"]) + ")"
            lines.append("  " + str(finding["what"]))
            lines.append("    " + str(finding["where"]) + when)
            if finding["because"]:
                lines.append("    why: " + finding["because"])
            for note in finding["notes"]:
                lines.append("    note: " + note)
            if finding["move_to"]:
                lines.append("    fix: " + finding["move_to"])
            lines.append("")

    if report.get("diff"):
        diff = report["diff"]
        lines.append("SINCE LAST RUN (" + str(diff.get("baseline_generated_at")) + ")")
        lines.append("-" * 78)
        if not (diff["new"] or diff["resolved"] or diff["changed"]):
            lines.append("  no change")
        for item in diff["new"]:
            lines.append("  + new     " + item["status"] + "  " + str(item["what"]) + "  (" + str(item["where"]) + ")")
        for item in diff["resolved"]:
            lines.append("  - gone    " + str(item["what"]) + "  (" + str(item["where"]) + ")")
        for item in diff["changed"]:
            lines.append("  ~ moved   " + str(item["what"]) + "  " + str(item["from"]) + " -> " + str(item["to"]))
        lines.append("")

    lines.append("LEDGER")
    lines.append("-" * 78)
    for row in report["ledger"]:
        detail = row.get("note") or ""
        lines.append("  " + row["status"].ljust(11) + row["source"].ljust(28) + detail)
    lines.append("")
    lines.append("Lifecycle dates from endoflife.date; package status from deps.dev and npm;")
    lines.append("action runtimes read from each action.yml at the pinned ref.")
    if report.get("ok_count_hidden"):
        lines.append(str(report["ok_count_hidden"]) + " supported item(s) not listed above; all of them are in --output=json.")
    return "\n".join(lines)


def render_summary(report):
    counts = report["counts"]
    parts = [str(counts["DEAD"]) + " dead",
             str(counts["DYING"]) + " dying <=" + str(report["horizon_days"]) + "d",
             str(counts["WATCH"]) + " watch",
             str(counts["OK"]) + " ok"]
    if counts["UNKNOWN"]:
        parts.append(str(counts["UNKNOWN"]) + " unknown")
    soonest = next((f for f in report["findings"] if f["status"] in ("DEAD", "DYING") and f["date"]), None)
    tail = ""
    if soonest:
        tail = DOT + "next: " + str(soonest["what"]) + " " + str(soonest["date"])
    return ("EOL Radar: " + DOT.join(parts) + DOT + "repo=" + report["repo"]
            + DOT + report["generated_at"] + tail)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def build_diff(findings, baseline_path):
    baseline = c.read_json(baseline_path)
    if not isinstance(baseline, dict):
        return {"error": "baseline could not be read: " + str(baseline_path),
                "new": [], "resolved": [], "changed": [], "baseline_generated_at": None}
    previous = {}
    for item in baseline.get("findings") or []:
        previous[finding_key(item)] = item
    current = {finding_key(f): f for f in findings}
    interesting = ("DEAD", "DYING", "WATCH")
    new_items, changed = [], []
    for key, finding in current.items():
        before = previous.get(key)
        if before is None:
            if finding["status"] in interesting:
                new_items.append(finding)
        elif before.get("status") != finding["status"]:
            changed.append({"what": finding["what"], "where": finding["where"],
                            "from": before.get("status"), "to": finding["status"]})
    resolved = [item for key, item in previous.items()
                if key not in current and item.get("status") in interesting]
    return {
        "baseline_generated_at": baseline.get("generated_at"),
        "new": sorted(new_items, key=sort_key),
        "resolved": resolved,
        "changed": changed,
    }


VALUE_FLAGS = {"--facts", "--today", "--horizon", "--repo-name", "--fail-on",
               "--enforcement", "--baseline", "--output"}


def main(argv):
    surface_paths = c.positionals(argv, VALUE_FLAGS)
    facts_path = c.arg_value(argv, "--facts")
    if not surface_paths or not facts_path:
        c.fail("join.py needs surface JSON files and --facts <file>")

    today_raw = c.arg_value(argv, "--today")
    today = parse_date(today_raw) if today_raw else datetime.date.today()
    if today is None:
        c.fail("--today must look like 2026-09-04")
    try:
        horizon = int(c.arg_value(argv, "--horizon", "90"))
    except ValueError:
        c.fail("--horizon must be a whole number of days")
    if horizon < 1 or horizon > 3650:
        c.fail("--horizon must be between 1 and 3650 days")

    repo_name = c.arg_value(argv, "--repo-name", "(unnamed)")
    fail_on = (c.arg_value(argv, "--fail-on", "none") or "none").lower()
    if fail_on not in ("none", "dying", "dead"):
        c.fail("--fail-on must be none, dying or dead")

    enforcement_path = c.arg_value(argv, "--enforcement") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "enforcement.json")
    enforcement = load_enforcement(enforcement_path)
    rules = enforcement.get("rules") or []

    facts_blob = c.read_json(facts_path)
    if not isinstance(facts_blob, dict):
        c.fail("could not read facts file: " + str(facts_path))
    facts = facts_blob.get("facts") or {}

    ledger = list(facts_blob.get("ledger") or [])
    findings = []
    for path in surface_paths:
        surface = c.read_json(path)
        if not isinstance(surface, dict):
            c.fail("could not read surface file: " + path)
        name = surface.get("surface", "?")
        subjects = surface.get("subjects") or []
        status = "ok"
        if surface.get("warning"):
            status = "skipped" if not subjects else "degraded"
        ledger.insert(0, {
            "source": "scan: " + name,
            "status": status,
            "attempted": len(subjects),
            "degraded": 0,
            "note": surface.get("warning") or (str(len(subjects)) + " item(s) from "
                                               + str(surface.get("files_scanned", 0)) + " file(s)"),
        })
        for subject in subjects:
            findings.append(evaluate(subject, facts, rules, today, horizon))

    findings.sort(key=sort_key)
    counts = {status: 0 for status in STATUS_ORDER}
    for finding in findings:
        counts[finding["status"]] = counts.get(finding["status"], 0) + 1

    report = {
        "tool": "eol-radar",
        "schema": 1,
        "generated_at": today.isoformat(),
        "repo": repo_name,
        "horizon_days": horizon,
        "counts": counts,
        "findings": findings,
        "ledger": ledger,
        "enforcement_verified": enforcement.get("verified"),
        "data_sources": [
            {"name": "endoflife.date", "url": "https://endoflife.date/api/v1/products"},
            {"name": "deps.dev", "url": "https://api.deps.dev/v3"},
            {"name": "npm registry", "url": "https://registry.npmjs.org"},
            {"name": "raw.githubusercontent.com", "url": "https://raw.githubusercontent.com"},
            {"name": "api.github.com", "url": "https://api.github.com"},
        ],
        "ok_count_hidden": counts["OK"],
    }

    baseline_path = c.arg_value(argv, "--baseline")
    if baseline_path:
        report["diff"] = build_diff(findings, baseline_path)

    view = (c.arg_value(argv, "--output", "human") or "human").lower()
    if view == "json":
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    elif view == "summary":
        sys.stdout.write(render_summary(report) + "\n")
    else:
        sys.stdout.write(render_human(report) + "\n")

    if fail_on == "dead" and counts["DEAD"]:
        sys.exit(2)
    if fail_on == "dying" and (counts["DEAD"] or counts["DYING"]):
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
