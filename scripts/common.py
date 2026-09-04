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


def emit(payload):
    """Write the step result as one canonical JSON line."""
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")


def fail(message, code=1):
    """Hard fault: dependents are blocked and rote offers --resume."""
    sys.stderr.write("eol-radar: " + str(message) + "\n")
    sys.exit(code)


def ok(surface, subjects, warning=None, scanned=0):
    return {
        "ok": True,
        "surface": surface,
        "subjects": subjects,
        "warning": warning,
        "files_scanned": scanned,
    }


def check_root(root):
    """Validate the scan root once, the same way in every scanner."""
    if not root:
        fail("root is empty; pass root=<path to a repository>")
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


def iter_files(root, max_depth=8):
    """Yield absolute paths under root, skipping vendored and generated trees."""
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
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
