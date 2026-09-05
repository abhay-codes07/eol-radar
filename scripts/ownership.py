"""Who has to do the work, and by which quarter.

A finding without an owner is a fact. A finding with an owner and a date is a
piece of work somebody can be asked about, which is the difference between a
report and a plan.

Ownership is read from CODEOWNERS, using GitHub's own resolution rule: the last
pattern that matches a path wins. The glob subset implemented here covers the
syntax CODEOWNERS files actually use; anything outside it is skipped rather
than guessed at, so a file is left unowned instead of wrongly attributed.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as c

# GitHub looks in these three places, in this order.
LOCATIONS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")

_OWNER = re.compile(r"^(?:@[A-Za-z0-9][A-Za-z0-9._/-]*|[^@\s]+@[^@\s]+\.[A-Za-z]{2,})$")


def pattern_to_regex(pattern):
    """Translate a CODEOWNERS pattern into an anchored regular expression.

    Returns None for a pattern this subset does not handle, so the caller can
    skip it rather than match the wrong files.
    """
    if not pattern or pattern.startswith("!") or "[" in pattern:
        return None
    if pattern == "*":
        return re.compile(r".*")

    anchored = pattern.startswith("/")
    directory = pattern.endswith("/")
    body = pattern.strip("/")
    if not body:
        return None

    out = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "*":
            if body[index:index + 3] == "**/":
                out.append(r"(?:.*/)?")
                index += 3
                continue
            if body[index:index + 2] == "**":
                out.append(r".*")
                index += 2
                continue
            out.append(r"[^/]*")
        elif char == "?":
            out.append(r"[^/]")
        elif char == "/":
            out.append("/")
        else:
            out.append(re.escape(char))
        index += 1

    expression = "".join(out)
    prefix = "" if anchored or "/" in body.rstrip("/") else r"(?:.*/)?"
    suffix = r"(?:/.*)?" if not directory else r"/.*"
    return re.compile("^" + prefix + expression + suffix + "$")


def parse(text):
    """Return [(pattern, regex, [owners])] in file order."""
    rules = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        pattern, owners = parts[0], [p for p in parts[1:] if _OWNER.match(p)]
        if not owners:
            continue
        regex = pattern_to_regex(pattern)
        if regex is None:
            continue
        rules.append((pattern, regex, owners))
    return rules


def load(root):
    for relative in LOCATIONS:
        path = os.path.join(root, relative.replace("/", os.sep))
        if os.path.isfile(path):
            text = c.read_text(path)
            if text:
                return {"source": relative, "rules": parse(text)}
    return {"source": None, "rules": []}


def owners_for(table, path):
    """Last matching pattern wins, which is GitHub's documented behaviour."""
    if not path:
        return []
    # Strip a leading "./" prefix only. lstrip("./") would strip characters and
    # turn ".github/workflows" into "github/workflows", which then misses every
    # pattern anchored at the dotfile directory.
    candidate = str(path).replace("\\", "/")
    while candidate.startswith("./"):
        candidate = candidate[2:]
    candidate = candidate.lstrip("/")
    found = []
    for _pattern, regex, owners in table.get("rules") or []:
        if regex.match(candidate):
            found = owners
    return found


def file_of(where):
    """Strip the :line suffix a finding carries."""
    text = str(where or "")
    head, sep, tail = text.rpartition(":")
    return head if sep and tail.isdigit() else text


def quarter_of(date):
    """2026-09-23 -> 2026-Q3. Returns None for a finding with no date."""
    if not date or len(str(date)) < 7:
        return None
    try:
        year, month = int(str(date)[:4]), int(str(date)[5:7])
    except ValueError:
        return None
    if not 1 <= month <= 12:
        return None
    return "%d-Q%d" % (year, (month - 1) // 3 + 1)


def annotate(findings, table):
    """Attach owners to every finding, in place, and report the coverage."""
    owned = 0
    for finding in findings:
        owners = owners_for(table, file_of(finding.get("where")))
        finding["owners"] = owners
        if owners:
            owned += 1
    return {"source": table.get("source"), "patterns": len(table.get("rules") or []),
            "findings_owned": owned, "findings_total": len(findings)}


def exposure(findings, statuses=("DEAD", "DYING", "WATCH")):
    """Group the work by quarter, and by owner inside each quarter."""
    quarters = {}
    for finding in findings:
        if finding.get("status") not in statuses:
            continue
        label = quarter_of(finding.get("date")) or "undated"
        bucket = quarters.setdefault(label, {"quarter": label, "count": 0,
                                             "owners": {}, "unowned": 0,
                                             "statuses": {}})
        bucket["count"] += 1
        status = finding.get("status")
        bucket["statuses"][status] = bucket["statuses"].get(status, 0) + 1
        owners = finding.get("owners") or []
        if not owners:
            bucket["unowned"] += 1
        for owner in owners:
            bucket["owners"][owner] = bucket["owners"].get(owner, 0) + 1

    ordered = []
    for label in sorted(quarters, key=lambda q: (q == "undated", q)):
        bucket = quarters[label]
        bucket["owners"] = sorted(bucket["owners"].items(), key=lambda kv: (-kv[1], kv[0]))
        ordered.append(bucket)
    return ordered


def by_owner(findings, statuses=("DEAD", "DYING")):
    """Total open work per owner, soonest deadline first."""
    totals = {}
    for finding in findings:
        if finding.get("status") not in statuses:
            continue
        for owner in finding.get("owners") or ["(unowned)"]:
            entry = totals.setdefault(owner, {"owner": owner, "count": 0, "soonest": None})
            entry["count"] += 1
            date = finding.get("date")
            if date and (entry["soonest"] is None or date < entry["soonest"]):
                entry["soonest"] = date
    return sorted(totals.values(), key=lambda e: (e["soonest"] or "9999-99-99", -e["count"]))
