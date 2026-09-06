"""Shared helpers for the eol-radar surface scanners.

Every scanner is an independent root step. It reads the filesystem only, never
the network, and prints exactly one JSON object to stdout.

Contract (rote's "degrade, never die" model):
  expected absence -> {"ok": true, "warning": "..."} and exit 0
  hard fault       -> message on stderr and a non-zero exit
"""

import json
import os
import re
import sys

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "bower_components", "vendor",
    "dist", "build", "out", "target", "coverage", "__pycache__",
    ".venv", "venv", "env", ".tox", ".nox", "site-packages",
    ".next", ".nuxt", ".svelte-kit", ".terraform", ".serverless",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle", ".idea",
    ".cache", ".parcel-cache", "cdk.out", ".aws-sam", ".eol-radar-cache",
}

MAX_FILE_BYTES = 8 * 1024 * 1024


# The keys whose size grows with the repository. With --out they go to the
# file and stdout carries their count instead.
UNBOUNDED_KEYS = ("subjects", "facts")


def digest(payload, target):
    """The stdout view of a result that was written to a file: every scalar
    and every small field, the unbounded lists as counts, and the file path."""
    view = {}
    for key, value in payload.items():
        if key in UNBOUNDED_KEYS and isinstance(value, (list, dict)):
            view[key + "_count"] = len(value)
        else:
            view[key] = value
    view["full_output"] = target
    return view


def emit(payload):
    """Write the step result as one canonical JSON line.

    Without --out, stdout is the whole result, which is what the command line
    runner and the tests read. With --out PATH the whole result goes to the
    file and stdout carries a digest of it. A Play step is a bare exec with no
    shell, so it cannot redirect stdout, the next step needs a file to read,
    and rote keeps only the first 65,536 bytes of a step's stdout: the digest
    never grows past a few hundred bytes, so the preview a reader sees is
    never cut, and nothing downstream depends on it.
    """
    target = arg_value(sys.argv[1:], "--out")
    if not target:
        sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
        return
    directory = os.path.dirname(os.path.abspath(target))
    try:
        # Five scanners start at the same instant inside a Play and all
        # create work/ together; exist_ok keeps the second one from failing.
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    except OSError as error:
        fail("could not write " + target + ": " + str(error))
    sys.stdout.write(json.dumps(digest(payload, target), sort_keys=True) + "\n")


def fail(message, code=1):
    """Hard fault: dependents are blocked and rote offers --resume."""
    sys.stderr.write("eol-radar: " + str(message) + "\n")
    sys.exit(code)


def ok(surface, subjects, warning=None, scanned=0):
    unreadable = sorted(set(UNREADABLE))
    return {
        "ok": True,
        "surface": surface,
        "subjects": subjects,
        "warning": warning,
        "files_scanned": scanned,
        # Directories the walk could not open. A scan that skipped them in
        # silence would be a partial scan reported as a complete one.
        "unreadable": unreadable[:50],
        "unreadable_count": len(unreadable),
    }


WORKSPACE_MARK = os.sep + ".rote" + os.sep + "workspaces" + os.sep


def check_root(root, in_play=False):
    """Validate the scan root once, the same way in every scanner.

    Inside a Play (--in-play) a step's working directory is rote's run
    workspace, never the caller's shell. A relative root such as `.` would
    therefore scan the workspace and report on rote's own scratch files with a
    straight face, which is a failure that reads as a success. It is refused,
    with the command that works instead.
    """
    if not root:
        fail("root is empty; pass root=<absolute path to a repository>")
    if in_play:
        if not os.path.isabs(root):
            fail("root=" + str(root) + " is relative. Inside a Play it resolves against rote's run "
                 "workspace (" + os.getcwd() + "), not your shell, so it would never reach your "
                 "repository. Pass an absolute path: root=$PWD or root=/path/to/repo")
        if WORKSPACE_MARK in os.path.abspath(root) + os.sep:
            fail("root=" + str(root) + " is inside rote's workspace directory, which holds this run's "
                 "own scratch files, not your repository. Pass an absolute path to the repository: "
                 "root=$PWD or root=/path/to/repo")
    if not os.path.isdir(root):
        fail("root is not a directory: " + str(root))
    return os.path.abspath(root)


def rel(root, path):
    """Repo-relative, forward-slash path so output is identical on any OS."""
    try:
        r = os.path.relpath(path, root)
    except ValueError:
        r = path
    return r.replace("\\", "/")


def read_text(path):
    """Tolerant read. Returns '' for unreadable or oversized files."""
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return ""
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return ""
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def read_json(path):
    text = read_text(path)
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


# Directories os.walk could not open in this process, repo-relative. os.walk
# ignores such errors by default; every scanner carries this list in its
# result instead, and join reports the scan as partial.
UNREADABLE = []


def _note_unreadable(root):
    def handler(error):
        path = getattr(error, "filename", None) or str(error)
        UNREADABLE.append(rel(root, path))
    return handler


def iter_files(root, max_depth=8):
    """Yield absolute paths under root, skipping vendored and generated trees.
    A directory that cannot be opened is recorded in UNREADABLE, not skipped
    in silence."""
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root, onerror=_note_unreadable(root)):
        depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        if depth >= max_depth:
            dirnames[:] = []
        else:
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".venv")]
        for name in filenames:
            yield os.path.join(dirpath, name)


def find_files(root, predicate, max_depth=8):
    """Collect files whose basename satisfies predicate, in stable order."""
    found = []
    for path in iter_files(root, max_depth=max_depth):
        if predicate(os.path.basename(path), path):
            found.append(path)
    found.sort(key=lambda p: rel(root, p))
    return found


def name_in(*names):
    lowered = set(n.lower() for n in names)
    return lambda base, _path: base.lower() in lowered


# --------------------------------------------------------------------------
# A deliberately small YAML reader.
#
# eol-radar declares python3 and git only, so it cannot import PyYAML. Every
# key it needs (runs-on, uses, image, runtime, node-version) is a plain scalar
# or a flow list on one line, which a line reader handles safely. Anything
# more exotic is skipped rather than guessed at.
# --------------------------------------------------------------------------

_PAIR = re.compile(r"^(?P<indent>\s*)(?P<dash>-\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_.\-]*)\s*:\s?(?P<value>.*)$")
_ITEM = re.compile(r"^(?P<indent>\s*)-\s+(?P<value>[^\s].*)$")


def strip_comment(value):
    """Drop a trailing # comment that is not inside quotes."""
    out = []
    quote = None
    for index, char in enumerate(value):
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            out.append(char)
            continue
        if char == "#" and (index == 0 or value[index - 1] in " \t"):
            break
        out.append(char)
    return "".join(out).strip()


def unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def scalar(value):
    return unquote(strip_comment(value))


def flow_list(value):
    """Turn '[a, b]' into ['a', 'b']; a bare scalar into a one-item list."""
    value = strip_comment(value)
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1]
        return [unquote(part) for part in inner.split(",") if part.strip()]
    value = unquote(value)
    return [value] if value else []


def yaml_pairs(text):
    """Yield (line_number, indent, key, raw_value) for every key: value line."""
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _PAIR.match(line)
        if not match:
            continue
        indent = len(match.group("indent"))
        if match.group("dash"):
            indent += 2
        yield number, indent, match.group("key"), match.group("value")


def yaml_items(text):
    """Yield (line_number, indent, value) for bare '- value' list entries."""
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if _PAIR.match(line):
            continue
        match = _ITEM.match(line)
        if match:
            yield number, len(match.group("indent")), unquote(strip_comment(match.group("value")))


def subject(kind, label, where, raw, lookup, note=None, fix=None, extra=None):
    """One thing in the repository that has a lifecycle question attached."""
    item = {
        "kind": kind,
        "label": label,
        "where": where,
        "raw": raw,
        "lookup": lookup,
        "note": note,
        "fix": fix,
    }
    if extra:
        item.update(extra)
    return item


def eol_lookup(product, cycle):
    return {"type": "eol", "product": product, "cycle": cycle}


def action_lookup(owner, repo, path, ref):
    return {"type": "action", "owner": owner, "repo": repo, "path": path, "ref": ref}


def package_lookup(system, name, version):
    return {"type": "package", "system": system, "name": name, "version": version}


def no_lookup(reason):
    return {"type": "none", "reason": reason}


def arg_value(argv, flag, default=None):
    """Tiny argument reader: --flag value or --flag=value."""
    for index, item in enumerate(argv):
        if item == flag and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith(flag + "="):
            return item.split("=", 1)[1]
    return default


def arg_flag(argv, flag):
    return flag in argv


def positionals(argv, value_flags):
    """Arguments that are not a flag and not a flag's value.

    value_flags names the options that consume the next argument, so
    `--horizon 90 a.json` yields ['a.json'] rather than ['90', 'a.json'].
    """
    out = []
    skip = False
    for index, item in enumerate(argv):
        if skip:
            skip = False
            continue
        if item.startswith("--"):
            if "=" not in item and item in value_flags:
                skip = True
            continue
        out.append(item)
    return out


def parse_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")
