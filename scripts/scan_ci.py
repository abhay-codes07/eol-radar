"""Surface 3 of 5: continuous integration.

The headline surface. Reads GitHub Actions workflows and reports:
  - runner labels, which retire on their own schedule
  - every `uses:` reference, so resolve.py can read the Node runtime the
    action actually declares at that ref rather than trusting the tag
  - local composite/node actions, read straight off disk
  - toolchain versions requested through setup-* actions
  - container and service images used by jobs

Filesystem only; the network half happens in resolve.py.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as c
import eol_data as ed

SURFACE = "ci"

# GitHub-hosted runner labels are an OS name followed by a version or "latest",
# optionally with an architecture or size suffix. Requiring that shape keeps
# ordinary hyphenated words such as a file called ubuntu-git.Dockerfile from
# being read as a runner.
_RUNNER = re.compile(
    r"\b((?:ubuntu|macos|windows)-(?:latest|\d[0-9.]*)"
    r"(?:-(?:arm|arm64|large|xlarge|vs\d+))?)\b")
_USES = re.compile(r"^\s*-?\s*uses\s*:\s*(?P<value>\S+)")
_EXPRESSION = re.compile(r"\$\{\{")

SETUP_KEYS = {
    "node-version": "nodejs",
    "node-version-file": None,
    "python-version": "python",
    "go-version": "go",
    "java-version": "eclipse-temurin",
    "ruby-version": "ruby",
    "php-version": "php",
    "dotnet-version": "dotnet",
    "terraform_version": "terraform",
    "terraform-version": "terraform",
}

FLOATING_RUNNERS = {"ubuntu-latest", "macos-latest", "windows-latest"}


def _is_workflow(base, path):
    lowered = base.lower()
    if not (lowered.endswith(".yml") or lowered.endswith(".yaml")):
        return False
    normalized = path.replace("\\", "/").lower()
    return "/.github/workflows/" in normalized


def _is_action_file(base, path):
    if base.lower() not in ("action.yml", "action.yaml"):
        return False
    return True


def _runner_subjects(root, path, text, subjects):
    where_file = c.rel(root, path)
    seen = set()
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for label in _RUNNER.findall(c.strip_comment(line)):
            key = (label, number)
            if key in seen:
                continue
            seen.add(key)
            where = where_file + ":" + str(number)
            if label in FLOATING_RUNNERS:
                subjects.append(c.subject(
                    "runner", label, where, stripped,
                    c.no_lookup("floating label: GitHub moves it to the current image"),
                    note="tracks whatever GitHub ships, so it does not expire",
                ))
                continue
            cycle = label
            if cycle.endswith("-arm"):
                cycle = cycle[:-4] + "-arm64"
            subjects.append(c.subject(
                "runner", label, where, stripped,
                c.eol_lookup("github-actions-runner-images", cycle),
                fix="move to a maintained runner image label",
            ))


def _uses_subjects(root, path, text, subjects, local_cache):
    where_file = c.rel(root, path)
    for number, line in enumerate(text.splitlines(), start=1):
        match = _USES.match(line)
        if not match:
            continue
        value = c.unquote(c.strip_comment(match.group("value")))
        if not value or _EXPRESSION.search(value):
            continue
        where = where_file + ":" + str(number)
        if value.split("@")[0].endswith((".yml", ".yaml")):
            # A reusable workflow, not an action: it has no runs.using of its
            # own. A local one is scanned where it lives; a remote one runs in
            # another repository and is reported rather than guessed at.
            local = value.startswith("./") or value.startswith("../")
            reason = ("local reusable workflow; its jobs are scanned as part of this repository"
                      if local else
                      "remote reusable workflow; its jobs live in another repository and are not inspected")
            subjects.append(c.subject("workflow", value, where, value, c.no_lookup(reason)))
            continue
        if value.startswith("./") or value.startswith("../"):
            _local_action(root, value, where, subjects, local_cache)
            continue
        if value.startswith("docker://"):
            reference = value[len("docker://"):]
            name, tag, namespace = ed.split_image(reference)
            product = ed.image_product(name, namespace) if name else None
            if product:
                cycle, note = ed.cycle_from_tag(product, tag)
                lookup = c.eol_lookup(product, cycle) if cycle else c.no_lookup(note or "unpinned")
                subjects.append(c.subject("image", reference, where, value, lookup, note=note))
            continue
        parsed = ed.normalize_action_ref(value)
        if not parsed:
            continue
        owner, repo, subpath, ref = parsed
        subjects.append(c.subject(
            "action", value, where, value,
            c.action_lookup(owner, repo, subpath, ref),
            fix="upgrade to a release that runs on node24",
        ))


def _local_action(root, value, where, subjects, local_cache):
    """Read a repo-local action's declared runtime straight off disk."""
    relative = value
    while relative.startswith("./") or relative.startswith("../"):
        relative = relative[2:] if relative.startswith("./") else relative[3:]
    relative = relative.strip("/")
    if relative in local_cache:
        using, source = local_cache[relative]
    else:
        using, source = None, None
        for candidate in ("action.yml", "action.yaml"):
            candidate_path = os.path.join(root, relative.replace("/", os.sep), candidate)
            if os.path.isfile(candidate_path):
                using = _read_using(c.read_text(candidate_path))
                source = c.rel(root, candidate_path)
                break
        local_cache[relative] = (using, source)
    if not using:
        shown = relative or "the repository root"
        exists = os.path.isdir(os.path.join(root, relative.replace("/", os.sep))) if relative else True
        reason = ("local action at " + shown + " has no runs.using in its action.yml"
                  if exists else
                  "no such path in the repository; it is probably created at run time by a "
                  "checkout step, so it cannot be inspected here")
        subjects.append(c.subject(
            "action", value, where, value, c.no_lookup(reason),
            note="local action at " + shown,
        ))
        return
    subjects.append(c.subject(
        "action", value + " (local)", where, value,
        c.no_lookup("local action read from disk"),
        note="declares runs.using: " + using + (" in " + source if source else ""),
        fix="update runs.using to node24" if using in ed.DEAD_ACTION_RUNTIMES else None,
        extra={"using": using, "resolved_locally": True},
    ))


def _read_using(text):
    in_runs = False
    for _number, indent, key, value in c.yaml_pairs(text):
        if key == "runs" and indent == 0:
            in_runs = True
            continue
        if indent == 0 and key != "runs":
            in_runs = False
        if in_runs and key == "using":
            return c.scalar(value).lower()
    return None


def _tool_subjects(root, path, text, subjects):
    where_file = c.rel(root, path)
    for number, _indent, key, value in c.yaml_pairs(text):
        product = SETUP_KEYS.get(key)
        if not product:
            continue
        raw = c.strip_comment(value)
        if not raw or _EXPRESSION.search(raw):
            continue
        candidates = c.flow_list(raw)
        # Several versions on one line is a compatibility matrix: the old ones
        # are there on purpose, to prove the code still runs on them. That is a
        # choice to report, not a pin about to break, so it is marked as such.
        matrix = len(candidates) > 1
        for candidate in candidates:
            version = ed.clean_version(candidate)
            if not version:
                continue
            subjects.append(c.subject(
                "ci-tool", product + " " + version + " (" + key + ")",
                where_file + ":" + str(number), candidate,
                c.eol_lookup(product, version),
                note=("one of " + str(len(candidates)) + " versions in a test matrix; "
                      "deliberately covering an old release is not a broken pin") if matrix else None,
                extra={"matrix": True} if matrix else None,
            ))


def _job_image_subjects(root, path, text, subjects):
    where_file = c.rel(root, path)
    for number, _indent, key, value in c.yaml_pairs(text):
        if key != "image":
            continue
        reference = c.scalar(value)
        if not reference or _EXPRESSION.search(reference):
            continue
        name, tag, namespace = ed.split_image(reference)
        product = ed.image_product(name, namespace) if name else None
        where = where_file + ":" + str(number)
        if not product:
            continue
        cycle, note = ed.cycle_from_tag(product, tag)
        lookup = c.eol_lookup(product, cycle) if cycle else c.no_lookup(note or "unpinned tag")
        subjects.append(c.subject("image", reference, where, "image: " + reference,
                                  lookup, note=note, extra={"origin": "workflow"}))


def _gitlab(root, path, subjects):
    text = c.read_text(path)
    where_file = c.rel(root, path)
    for number, _indent, key, value in c.yaml_pairs(text):
        if key != "image":
            continue
        reference = c.scalar(value)
        if not reference:
            continue
        name, tag, namespace = ed.split_image(reference)
        product = ed.image_product(name, namespace) if name else None
        if not product:
            continue
        cycle, note = ed.cycle_from_tag(product, tag)
        lookup = c.eol_lookup(product, cycle) if cycle else c.no_lookup(note or "unpinned tag")
        subjects.append(c.subject("image", reference, where_file + ":" + str(number),
                                  "image: " + reference, lookup, note=note,
                                  extra={"origin": "gitlab-ci"}))


def scan(root, max_depth=8):
    subjects = []
    local_cache = {}
    workflows = c.find_files(root, _is_workflow, max_depth)
    for path in workflows:
        text = c.read_text(path)
        _runner_subjects(root, path, text, subjects)
        _uses_subjects(root, path, text, subjects, local_cache)
        _tool_subjects(root, path, text, subjects)
        _job_image_subjects(root, path, text, subjects)

    # Actions defined in this repository that no workflow above already covered.
    already = set()
    for relative, (using, source) in local_cache.items():
        if source:
            already.add(source)
    for path in c.find_files(root, _is_action_file, max_depth):
        where = c.rel(root, path)
        if where in already:
            continue
        using = _read_using(c.read_text(path))
        if not using:
            continue
        subjects.append(c.subject(
            "action", where + " (this repo)", where + ":1", "runs.using: " + using,
            c.no_lookup("action defined in this repository"),
            note="declares runs.using: " + using,
            fix="update runs.using to node24" if using in ed.DEAD_ACTION_RUNTIMES else None,
            extra={"using": using, "resolved_locally": True},
        ))

    gitlab = c.find_files(root, c.name_in(".gitlab-ci.yml", ".gitlab-ci.yaml"), max_depth)
    for path in gitlab:
        _gitlab(root, path, subjects)

    return subjects, len(workflows) + len(gitlab)


def main(argv):
    root = c.check_root(c.arg_value(argv, "--root", "."))
    depth = int(c.arg_value(argv, "--depth", "8"))
    subjects, scanned = scan(root, depth)
    warning = None if scanned else "no CI workflows found (.github/workflows, .gitlab-ci.yml)"
    c.emit(c.ok(SURFACE, subjects, warning, scanned))


if __name__ == "__main__":
    main(sys.argv[1:])
