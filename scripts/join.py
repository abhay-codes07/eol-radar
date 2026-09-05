"""Join what is in the repository with what is known about its lifecycle, and
say what breaks first.

Reads the surface files and the resolver's facts, applies the curated
enforcement table, then renders the three views rote asks for: human, summary
and canonical JSON. No fact present in the JSON is absent from the human view.
"""

import datetime
import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as c
import eol_data as ed
import migrate
import ownership

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



def _write_bytes_safe(text):
    """Write a diff without newline translation.

    Windows text-mode stdout rewrites every LF as CRLF, which makes a patch stop
    matching the files it was generated from.
    """
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(newline="")
        except (ValueError, OSError):
            pass
    sys.stdout.write(text)


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
    rules = list(data.get("rules") or [])
    rules.extend(expand_lambda_phases(data.get("aws_lambda_phases") or {}))
    lambda_block = data.get("aws_lambda_phases") or {}
    return {"verified": data.get("verified"), "rules": rules,
            "schema": data.get("schema"), "note": data.get("note"),
            "lambda_supported": lambda_block.get("supported") or []}


def expand_lambda_phases(block):
    """Turn the compact Lambda table into one rule per block-create date.

    Block-update is left to endoflife.date, which publishes it as this
    product's end-of-life date. Block-create lands earlier and nothing else
    publishes it, so it is the date worth surfacing.
    """
    source = block.get("source")
    checked = block.get("checked")
    rules = []
    for runtime, phases in sorted((block.get("runtimes") or {}).items()):
        create = phases.get("block_create")
        if not create:
            continue
        detail = ["Deprecated " + str(phases.get("deprecated")) + "."]
        detail.append("New functions blocked from " + create + ".")
        if phases.get("block_update"):
            detail.append("Updates to existing functions blocked from "
                          + phases["block_update"] + ", which is the date that strands a service.")
        detail.append("Functions already deployed keep running.")
        if phases.get("os"):
            detail.append("Runs on " + phases["os"] + ".")
        if checked:
            detail.append("AWS has moved these dates before; read from the AWS "
                          "documentation on " + checked + ".")
        rules.append({
            "id": "aws-lambda-block-create-" + runtime,
            "match": {"kind": "cloud-runtime", "platform": "aws-lambda", "cycle": [runtime]},
            "date": create,
            "headline": "AWS Lambda blocks new " + runtime + " functions",
            "detail": " ".join(detail),
            "fix": "Move the function to a Lambda runtime that is still supported.",
            "source": source,
        })
    return rules


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
    finding["_matrix"] = bool(subject.get("matrix"))

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
        elif reason.startswith("floating label") or reason.startswith("local reusable workflow"):
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

    # A verified upgrade target beats any generic advice: we read that release's
    # action.yml and confirmed it runs on a live runtime, so name it.
    if finding.get("upgrade_to"):
        finding["move_to"] = "upgrade to " + finding["upgrade_to"]
        if finding.get("upgrade_using"):
            finding["move_to"] += " (runs on " + str(finding["upgrade_using"]) + ")"

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
    finding["product"] = product
    release = cycles[cycle]
    eol_date = parse_date(release.get("eol"))
    eoas_date = parse_date(release.get("eoas"))
    eoes_date = parse_date(release.get("eoes"))
    finding["cycle"] = cycle
    finding["date"] = eol_date
    finding["date_kind"] = "eol"
    label = str(product) + " " + str(cycle)
    requested = str(lookup.get("cycle") or "")
    if requested and requested != cycle and cycle.startswith(requested + "."):
        finding["notes"].append("pins only " + requested + ", which floats to the newest "
                                + requested + ".x release; evaluated as " + cycle)
    if eol_date:
        finding["because"] = label + " end of life " + eol_date.isoformat()
    elif release.get("is_eol"):
        finding["because"] = label + " is marked end of life"
    elif release.get("is_maintained"):
        # Maintained with no announced end date is the normal state of a current
        # release line, not a gap in the data, so it is not reported as unknown.
        finding["status"] = "OK"
        finding["because"] = label + " is maintained; no end-of-life date announced yet"
    else:
        finding["because"] = label + " has no published end-of-life date"
    if eoas_date:
        finding["eoas"] = eoas_date.isoformat()
    if eoes_date:
        finding["notes"].append("extended security maintenance until " + eoes_date.isoformat())
    if release.get("latest"):
        finding["notes"].append("latest in this line: " + str(release["latest"]))
    if product == "github-actions-runner-images":
        replacement = _runner_upgrade(cycles, cycle)
        if replacement:
            finding["upgrade_to"] = replacement
            finding["move_to"] = "move to " + replacement
        return

    target = fact.get("newest_lts") or fact.get("newest_maintained")
    if target and target != cycle and not finding["move_to"]:
        suffix = " (current LTS)" if fact.get("newest_lts") == target else ""
        finding["move_to"] = "move to " + str(product) + " " + str(target) + suffix


def _runner_upgrade(cycles, current):
    """The newest maintained runner label of the same family and architecture.

    ubuntu-20.04 should become ubuntu-24.04, not macos-26, and an arm label
    stays on arm. The version is compared numerically rather than trusting the
    order of the cycles mapping, which arrives key-sorted, so ubuntu-22.04 does
    not win over ubuntu-24.04 on a string comparison. Toolchain variants such as
    windows-2025-vs2026 are skipped: switching image flavour is not an upgrade.
    """
    family = str(current).split("-")[0]
    wants_arm = "arm" in str(current)
    best, best_key = None, None
    for name, release in cycles.items():
        if not release.get("is_maintained") or release.get("is_eol"):
            continue
        if not name.startswith(family + "-"):
            continue
        if ("arm" in name) != wants_arm:
            continue
        rest = name[len(family) + 1:]
        match = re.match(r"^(\d+(?:\.\d+)*)", rest)
        if not match:
            continue
        tail = rest[match.end():].strip("-")
        if tail and tail not in ("arm", "arm64"):
            continue
        key = tuple(int(part) for part in match.group(1).split("."))
        if best_key is None or key > best_key:
            best, best_key = name, key
    return best if best and best != current else None
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
    upgrade = fact.get("upgrade")
    if upgrade and upgrade.get("ref"):
        target = "%s/%s@%s" % (lookup.get("owner"), lookup.get("repo"), upgrade["ref"])
        if lookup.get("path"):
            target = "%s/%s/%s@%s" % (lookup.get("owner"), lookup.get("repo"),
                                      lookup["path"], upgrade["ref"])
        finding["upgrade_to"] = target
        finding["upgrade_using"] = upgrade.get("using")


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

    # A retired version inside a test matrix is covered on purpose. It stays in
    # the report with its date, but as something to watch rather than a break.
    if finding.pop("_matrix", False) and finding["status"] in ("DEAD", "DYING"):
        finding["status"] = "WATCH"

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

def _grouped(findings):
    """Collapse the same finding at many lines into one entry with its places.

    The JSON keeps one finding per location, which the patch and migration
    views need. The human view says it once and lists where, because the same
    SHA-pinned action at fourteen lines is one fact, not fourteen.
    """
    order = []
    places = {}
    for finding in findings:
        key = (finding.get("kind"), finding.get("what"), finding.get("because"),
               finding.get("date"), finding.get("move_to"))
        if key not in places:
            places[key] = []
            order.append((key, finding))
        places[key].append(str(finding.get("where")))
    return [(finding, places[key]) for key, finding in order]


def render_human(report):
    lines = []
    counts = report["counts"]
    head = ("EOL Radar" + DOT + report["repo"] + DOT + "checked " + report["generated_at"]
            + DOT + "horizon " + str(report["horizon_days"]) + " days")
    lines.append(head)
    lines.append("=" * min(len(head), 78))
    lines.append("")

    summary_bits = [_count_phrase(report, status, status.lower())
                    for status in STATUS_ORDER if counts[status]]
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
        for finding, places in _grouped(group):
            when = ""
            if finding["date"]:
                verb = "died" if (finding["days"] is not None and finding["days"] <= 0) else "breaks"
                when = "  " + verb + " " + finding["date"] + " (" + human_days(finding["days"]) + ")"
            lines.append("  " + str(finding["what"]))
            lines.append("    " + str(finding["where"]) + when)
            if len(places) > 1:
                extra = places[1:]
                listed = ", ".join(extra[:6]) + (", and %d more" % (len(extra) - 6) if len(extra) > 6 else "")
                lines.append("    also at " + str(len(extra)) + " other place(s): " + listed)
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

    exposure = report.get("exposure_by_quarter") or []
    if exposure:
        lines.append("EXPOSURE BY QUARTER" + DASH + "when the work lands")
        lines.append("-" * 78)
        for bucket in exposure:
            parts = ["%d %s" % (count, status.lower())
                     for status, count in sorted(bucket["statuses"].items(),
                                                 key=lambda kv: STATUS_RANK.get(kv[0], 9))]
            lines.append("  %-10s %-34s" % (bucket["quarter"], ", ".join(parts)))
            if bucket["owners"]:
                shown = ", ".join("%s (%d)" % (owner, count) for owner, count in bucket["owners"][:4])
                lines.append("      owners: " + shown)
            if bucket["unowned"]:
                lines.append("      unowned: %d" % bucket["unowned"])
        source = (report.get("ownership") or {}).get("source")
        if source:
            lines.append("  owners read from " + source)
        else:
            lines.append("  no CODEOWNERS file, so nothing is attributed to a team")
        lines.append("")

    lines.append("LEDGER")
    lines.append("-" * 78)
    for row in report["ledger"]:
        detail = row.get("note") or ""
        lines.append("  " + row["status"].ljust(12) + row["source"].ljust(28) + detail)
    lines.append("")
    lines.append("Lifecycle dates from endoflife.date; package status from deps.dev and npm;")
    lines.append("action runtimes read from each action.yml at the pinned ref.")
    if report.get("ok_count_hidden"):
        lines.append(str(report["ok_count_hidden"]) + " supported item(s) not listed above; all of them are in --output=json.")
    return "\n".join(lines)



# ---------------------------------------------------------------------------
# Patch view: the mechanical half of the fix, as a diff you can apply
# ---------------------------------------------------------------------------

def render_patch(report, root):
    """Emit a unified diff for the fixes that are safe to make mechanically.

    Only findings with a verified replacement are included: an action upgrade
    whose action.yml we actually read, or a runner label endoflife.date still
    lists as maintained. Nothing is written to disk here; the diff goes to
    stdout so a human can read it before `git apply` ever sees it.
    """
    edits = {}
    skipped = []
    for finding in report["findings"]:
        target = finding.get("upgrade_to")
        where = str(finding.get("where") or "")
        head, sep, tail = where.rpartition(":")
        if not target or not sep or not tail.isdigit():
            continue
        if finding.get("kind") not in ("action", "runner"):
            continue
        token = finding["raw"] if finding.get("kind") == "action" else finding.get("what")
        if not token or token not in (finding.get("raw") or "") and finding.get("kind") == "action":
            continue
        edits.setdefault(head, []).append((int(tail), str(token), str(target), finding))

    lines_out = []
    changed_files = 0
    for path in sorted(edits):
        absolute = os.path.join(root, path.replace("/", os.sep))
        original = c.read_text(absolute)
        if not original:
            skipped.append(path + " (unreadable)")
            continue
        # keepends preserves each line's own terminator, so a file checked out
        # with CRLF produces a patch that still matches it byte for byte.
        before = original.splitlines(keepends=True)
        after = list(before)
        touched = 0
        for number, token, target, finding in sorted(edits[path]):
            index = number - 1
            if index < 0 or index >= len(after) or token not in after[index]:
                skipped.append(path + ":" + str(number) + " (line moved or already changed)")
                continue
            after[index] = after[index].replace(token, target)
            touched += 1
        if not touched:
            continue
        changed_files += 1
        lines_out.extend(difflib.unified_diff(
            before, after, fromfile="a/" + path, tofile="b/" + path, n=3))

    if not lines_out:
        return "\n".join([
            "# eol-radar: nothing to patch mechanically.",
            "# Runtime pins, base images and dependencies need a human decision,",
            "# so they are reported but never rewritten.",
        ]) + "\n"
    header = ["# eol-radar patch: " + str(changed_files) + " file(s)",
              "# Every replacement below was verified against the upstream source.",
              "# Review it, then: git apply <this file>"]
    if skipped:
        header.append("# skipped: " + "; ".join(skipped[:5]))
    # Each diff line already carries the terminator of the file it came from, so
    # the body is joined with nothing and only topped up if the last line lacked one.
    body = "".join(lines_out)
    if not body.endswith("\n"):
        body += "\n"
    return "\n".join(header) + "\n\n" + body


def _count_phrase(report, status, label):
    """'3 dying' normally; '3 dying at 188 places' when one fact repeats."""
    total = report["counts"].get(status, 0)
    distinct = (report.get("distinct") or {}).get(status, total)
    if distinct and distinct < total:
        return "%d %s at %d places" % (distinct, label, total)
    return "%d %s" % (total, label)


def render_summary(report):
    counts = report["counts"]
    parts = [_count_phrase(report, "DEAD", "dead"),
             _count_phrase(report, "DYING", "dying <=" + str(report["horizon_days"]) + "d"),
             _count_phrase(report, "WATCH", "watch"),
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
               "--enforcement", "--baseline", "--output", "--root", "--migrate", "--out"}


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

    # A Play step cannot compute a basename, so when no name is given the
    # scanned directory's own name is used rather than "(unnamed)".
    repo_name = c.arg_value(argv, "--repo-name")
    if not repo_name:
        root_arg = c.arg_value(argv, "--root", ".")
        repo_name = os.path.basename(os.path.abspath(root_arg)) or "(unnamed)"
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
    # The same action at 188 lines is one fact, and a reader deciding how bad a
    # repository is needs to know it is three problems, not 188.
    distinct = {}
    for status in STATUS_ORDER:
        distinct[status] = len({(f.get("kind"), f.get("what"), f.get("because"))
                                for f in findings if f["status"] == status})

    # Who has to do the work, and by when.
    scan_root = c.arg_value(argv, "--root", ".")
    codeowners = ownership.load(scan_root) if os.path.isdir(scan_root) else {"source": None, "rules": []}
    coverage = ownership.annotate(findings, codeowners)

    report = {
        "tool": "eol-radar",
        "schema": 1,
        "generated_at": today.isoformat(),
        "repo": repo_name,
        "horizon_days": horizon,
        "counts": counts,
        "distinct": distinct,
        "findings": findings,
        "ledger": ledger,
        "enforcement_verified": enforcement.get("verified"),
        "ownership": coverage,
        "exposure_by_quarter": ownership.exposure(findings),
        "work_by_owner": ownership.by_owner(findings),
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

    # A migration replaces the ordinary views: it answers a different question,
    # which is "move this repository off that runtime everywhere at once".
    spec = c.arg_value(argv, "--migrate")
    if spec:
        product, _, target = spec.partition("=")
        product = product.strip()
        target = target.strip()
        if not target:
            fact = facts.get("eol:" + product) or {}
            target = fact.get("newest_lts") or fact.get("newest_maintained") or ""
        if not product or not target:
            c.fail("--migrate needs a product, and a target this run could not infer. "
                   "Try --migrate " + (product or "python") + "=<version>")
        result = migrate.plan(report, scan_root, product, target,
                              enforcement.get("lambda_supported"))
        _write_bytes_safe(migrate.render(result))
        if not result["edits"]:
            sys.exit(3)
        return

    view = (c.arg_value(argv, "--output", "human") or "human").lower()
    if view == "json":
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    elif view == "summary":
        sys.stdout.write(render_summary(report) + "\n")
    elif view == "patch":
        _write_bytes_safe(render_patch(report, scan_root))
    else:
        sys.stdout.write(render_human(report) + "\n")

    if fail_on == "dead" and counts["DEAD"]:
        sys.exit(2)
    if fail_on == "dying" and (counts["DEAD"] or counts["DYING"]):
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
