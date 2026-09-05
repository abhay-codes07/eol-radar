"""Move a repository off a dead runtime, in every place it is declared.

Bumping one file is easy and useless. A Python version is pinned in the
Dockerfile, the CI matrix, `requires-python`, `.python-version` and the Lambda
runtime identifier, and a build only goes green when all of them agree. This
plans that change as one coordinated edit and says plainly what it cannot do.

It writes nothing. The output is a diff and a checklist.
"""

import difflib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as c
import ownership

# Files whose entire contents are the version, so the whole value is replaced
# rather than a substring of it.
PIN_FILES = {".nvmrc", ".node-version", ".python-version", ".ruby-version",
             ".go-version", ".java-version", ".php-version", ".terraform-version"}

# How a language maps onto a Lambda runtime identifier.
LAMBDA_IDS = {
    "nodejs": lambda target: "nodejs" + target + ".x",
    "python": lambda target: "python" + target,
    "ruby": lambda target: "ruby" + target,
    "dotnet": lambda target: "dotnet" + target,
}

# Work a text substitution cannot do, listed so nobody assumes the diff is the
# whole job.
FOLLOW_UP = {
    "python": [
        "Regenerate the lockfile: poetry lock, uv lock, or pip-compile.",
        "Re-run the test suite on the new interpreter before merging.",
        "Check for syntax or standard-library changes between the two releases.",
    ],
    "nodejs": [
        "Reinstall so the lockfile records the new engine: npm install, pnpm install, or yarn.",
        "Re-run the test suite on the new runtime before merging.",
        "Check native modules, which often need rebuilding across a major.",
    ],
}


def _guarded(cycle):
    """Match a version where it stands alone, not inside a longer number.

    '20' matches in 'node:20-alpine' and in '>=20.0.0', but not in '2026'.
    """
    return re.compile(r"(?<![\d.])" + re.escape(str(cycle)) + r"(?![\d])")


def _lambda_target(product, target, supported):
    builder = LAMBDA_IDS.get(product)
    if not builder:
        return None
    identifier = builder(target)
    if supported and identifier not in supported:
        return None
    return identifier


def rewrite_for(finding, product, target, supported):
    """The (old, new) token pair for one finding, or None if it needs a human.

    Returns a third element saying whether the whole line value is replaced.
    """
    kind = finding.get("kind")
    cycle = finding.get("cycle")
    raw = (finding.get("raw") or "").strip()
    path = ownership.file_of(finding.get("where"))
    base = path.rsplit("/", 1)[-1]

    if not cycle or str(cycle) == str(target):
        return None

    if kind == "runtime" and base in PIN_FILES:
        return (raw, target, True)

    if kind == "cloud-runtime":
        identifier = _lambda_target(product, target, supported)
        if not identifier:
            return None
        return (str(cycle), identifier, False)

    if kind in ("runtime", "image", "ci-tool", "framework"):
        return (str(cycle), str(target), False)

    return None


def _why_not(line, expected):
    """Explain a refusal in terms the reader can act on."""
    if "$" in line:
        return ("the version comes from a variable on another line; "
                "change that definition instead")
    if not str(expected):
        return "nothing identifiable to replace"
    return ("expected " + repr(str(expected)) + " here, but this line declares it "
            "somewhere else; edit it by hand")


def _concerns(finding, product):
    """Is this finding part of moving the repository off `product`?

    A managed runtime is filed under its platform rather than its language, so
    an AWS Lambda finding carries product 'aws-lambda' and cycle 'python3.9'.
    Migrating Python has to move that identifier too, or the deploy keeps the
    old interpreter after every other file has moved.
    """
    if finding.get("product") == product:
        return True
    cycle = str(finding.get("cycle") or "")
    return finding.get("kind") == "cloud-runtime" and cycle.startswith(product)


def plan(report, root, product, target, supported=None):
    """Work out every edit, and everything that cannot be edited."""
    edits = {}
    covered = []
    manual = []
    for finding in report.get("findings") or []:
        if not _concerns(finding, product):
            continue
        if finding.get("status") not in ("DEAD", "DYING", "WATCH"):
            continue
        where = str(finding.get("where") or "")
        head, sep, tail = where.rpartition(":")
        rewrite = rewrite_for(finding, product, target, supported)
        if not rewrite or not sep or not tail.isdigit():
            manual.append({"what": finding.get("what"), "where": where,
                           "why": "no safe text substitution for this declaration"})
            continue
        old, new, whole = rewrite
        edits.setdefault(head, []).append((int(tail), old, new, whole, finding))
        covered.append(finding)

    diff_lines = []
    applied = []
    skipped = []
    for path in sorted(edits):
        absolute = os.path.join(root, path.replace("/", os.sep))
        original = c.read_text(absolute)
        if not original:
            skipped.append({"where": path, "why": "file could not be read"})
            continue
        before = original.splitlines(keepends=True)
        after = list(before)
        touched = 0
        for number, old, new, whole, finding in sorted(edits[path]):
            index = number - 1
            if index < 0 or index >= len(after):
                skipped.append({"where": path + ":" + str(number), "why": "line is out of range"})
                continue
            line = after[index]
            if whole:
                replaced = line.replace(old, new, 1) if old and old in line else None
            else:
                pattern = _guarded(old)
                replaced = pattern.sub(new, line, count=1) if pattern.search(line) else None
            if replaced is None or replaced == line:
                skipped.append({"where": path + ":" + str(number),
                                "why": _why_not(line, old)})
                continue
            after[index] = replaced
            touched += 1
            applied.append({"what": finding.get("what"), "where": path + ":" + str(number),
                            "from": old, "to": new,
                            "owners": finding.get("owners") or []})
        if touched:
            diff_lines.extend(difflib.unified_diff(
                before, after, fromfile="a/" + path, tofile="b/" + path, n=3))

    owners = sorted({owner for item in applied for owner in item["owners"]})
    return {
        "product": product,
        "target": target,
        "files_changed": len({item["where"].rsplit(":", 1)[0] for item in applied}),
        "edits": applied,
        "skipped": skipped,
        "manual": manual,
        "owners": owners,
        "follow_up": FOLLOW_UP.get(product, ["Re-run the test suite before merging."]),
        "diff": "".join(diff_lines),
    }


def render(result):
    """A patch with the plan above it, commented so git apply still accepts it."""
    lines = ["# eol-radar migration: " + result["product"] + " -> " + result["target"],
             "# " + str(len(result["edits"])) + " edit(s) across "
             + str(result["files_changed"]) + " file(s)"]
    if result["owners"]:
        lines.append("# owners: " + ", ".join(result["owners"]))
    for item in result["edits"]:
        lines.append("#   " + item["where"] + "  " + item["from"] + " -> " + item["to"])
    if result["skipped"]:
        lines.append("# not edited:")
        for item in result["skipped"][:8]:
            lines.append("#   " + item["where"] + "  " + item["why"])
    if result["manual"]:
        lines.append("# needs a human:")
        for item in result["manual"][:8]:
            lines.append("#   " + str(item["what"]) + "  " + item["where"] + "  " + item["why"])
    lines.append("# after applying:")
    for step in result["follow_up"]:
        lines.append("#   - " + step)
    if not result["diff"]:
        lines.append("#")
        lines.append("# Nothing could be migrated automatically.")
        return "\n".join(lines) + "\n"
    lines.append("# Review it, then: git apply <this file>")
    body = result["diff"]
    if not body.endswith("\n"):
        body += "\n"
    return "\n".join(lines) + "\n\n" + body
