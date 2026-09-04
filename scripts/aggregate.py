"""Roll several per-repository reports into one account-wide view.

A single repository tells you what to fix. A whole account tells you what to
fix first, because the same deadline usually takes out several repositories at
once and the same upgrade usually clears most of them.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as c
import join

VALUE_FLAGS = {"--output", "--owner", "--today", "--horizon", "--top"}


def load(paths):
    reports = []
    for path in paths:
        data = c.read_json(path)
        if isinstance(data, dict) and data.get("findings") is not None:
            reports.append(data)
    return reports


def build(reports, owner, today, horizon, top=10):
    repos = []
    by_date = {}
    by_what = {}
    for report in reports:
        name = report.get("repo") or "(unnamed)"
        counts = report.get("counts") or {}
        repos.append({
            "repo": name,
            "dead": counts.get("DEAD", 0),
            "dying": counts.get("DYING", 0),
            "watch": counts.get("WATCH", 0),
            "unknown": counts.get("UNKNOWN", 0),
            "ok": counts.get("OK", 0),
        })
        for finding in report.get("findings") or []:
            if finding.get("status") not in ("DEAD", "DYING"):
                continue
            what = str(finding.get("what"))
            entry = by_what.setdefault(what, {
                "what": what, "kind": finding.get("kind"), "repos": set(),
                "count": 0, "move_to": finding.get("move_to"),
                "date": finding.get("date"), "status": finding.get("status"),
            })
            entry["repos"].add(name)
            entry["count"] += 1
            if not entry["move_to"] and finding.get("move_to"):
                entry["move_to"] = finding["move_to"]

            date = finding.get("date")
            if not date:
                continue
            bucket = by_date.setdefault(date, {
                "date": date, "days": finding.get("days"),
                "repos": set(), "count": 0, "reasons": {},
            })
            bucket["repos"].add(name)
            bucket["count"] += 1
            reason = finding.get("because") or ""
            bucket["reasons"][reason] = bucket["reasons"].get(reason, 0) + 1

    def shrink(entry):
        out = dict(entry)
        out["repos"] = sorted(entry["repos"])
        out["repo_count"] = len(out["repos"])
        return out

    deadlines = [shrink(b) for b in by_date.values()]
    for bucket in deadlines:
        reasons = bucket.pop("reasons", {})
        bucket["reason"] = max(reasons.items(), key=lambda kv: kv[1])[0] if reasons else None
    # Only a date shared by more than one repository is worth a headline.
    shared = sorted([d for d in deadlines if d["repo_count"] > 1], key=lambda d: d["date"])

    offenders = sorted((shrink(e) for e in by_what.values()),
                       key=lambda e: (-e["repo_count"], e["date"] or "9999", e["what"]))

    repos.sort(key=lambda r: (-(r["dead"] + r["dying"]), r["repo"]))
    return {
        "tool": "eol-radar",
        "schema": 1,
        "view": "account",
        "owner": owner,
        "generated_at": today,
        "horizon_days": horizon,
        "repositories_scanned": len(repos),
        "repositories_with_dead": sum(1 for r in repos if r["dead"]),
        "repositories_with_dying": sum(1 for r in repos if r["dying"]),
        "repositories_clean": sum(1 for r in repos if not r["dead"] and not r["dying"]),
        "total_dead": sum(r["dead"] for r in repos),
        "total_dying": sum(r["dying"] for r in repos),
        "shared_deadlines": shared,
        "top_offenders": offenders[:top],
        "repositories": repos,
    }


def render_human(summary):
    dot, dash = join.DOT, join.DASH
    lines = []
    head = ("EOL Radar" + dot + str(summary["owner"]) + dot
            + str(summary["repositories_scanned"]) + " repositories" + dot
            + "checked " + str(summary["generated_at"]))
    lines.append(head)
    lines.append("=" * min(len(head), 78))
    lines.append("")
    lines.append("  %d already carrying something dead, %d with something dying, %d clean"
                 % (summary["repositories_with_dead"], summary["repositories_with_dying"],
                    summary["repositories_clean"]))
    lines.append("")

    if summary["shared_deadlines"]:
        lines.append("SHARED DEADLINES" + dash + "one date, several repositories")
        lines.append("-" * 78)
        for bucket in summary["shared_deadlines"]:
            when = join.human_days(bucket.get("days"))
            lines.append("  %s  %-14s %d finding(s) across %d repositories"
                         % (bucket["date"], when, bucket["count"], bucket["repo_count"]))
            if bucket.get("reason"):
                lines.append("      " + bucket["reason"])
            listed = ", ".join(bucket["repos"][:8])
            if bucket["repo_count"] > 8:
                listed += ", and %d more" % (bucket["repo_count"] - 8)
            lines.append("      " + listed)
            lines.append("")

    if summary["top_offenders"]:
        lines.append("ONE FIX, MANY REPOSITORIES" + dash + "ranked by reach")
        lines.append("-" * 78)
        for entry in summary["top_offenders"]:
            lines.append("  %-42s %2d repo(s)" % (entry["what"][:42], entry["repo_count"]))
            if entry.get("move_to"):
                lines.append("      " + entry["move_to"])
        lines.append("")

    lines.append("BY REPOSITORY")
    lines.append("-" * 78)
    lines.append("  %-40s %6s %6s %6s" % ("repository", "dead", "dying", "watch"))
    for row in summary["repositories"]:
        lines.append("  %-40s %6d %6d %6d"
                     % (row["repo"][:40], row["dead"], row["dying"], row["watch"]))
    lines.append("")
    lines.append("Run eol-radar against any one of these for the file and line of every finding.")
    return "\n".join(lines)


def render_summary(summary):
    return ("EOL Radar" + join.DOT + str(summary["owner"]) + join.DOT
            + "%d repositories" % summary["repositories_scanned"] + join.DOT
            + "%d dead" % summary["total_dead"] + join.DOT
            + "%d dying" % summary["total_dying"] + join.DOT
            + "%d clean" % summary["repositories_clean"] + join.DOT
            + str(summary["generated_at"]))


def main(argv):
    paths = c.positionals(argv, VALUE_FLAGS)
    if not paths:
        c.fail("aggregate.py needs one or more per-repository JSON reports")
    reports = load(paths)
    if not reports:
        c.fail("none of the given files looked like an eol-radar report")
    owner = c.arg_value(argv, "--owner", "(account)")
    today = c.arg_value(argv, "--today") or reports[0].get("generated_at")
    horizon = int(c.arg_value(argv, "--horizon", "90"))
    top = int(c.arg_value(argv, "--top", "10"))
    summary = build(reports, owner, today, horizon, top)

    view = (c.arg_value(argv, "--output", "human") or "human").lower()
    if view == "json":
        sys.stdout.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    elif view == "summary":
        sys.stdout.write(render_summary(summary) + "\n")
    else:
        sys.stdout.write(render_human(summary) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
