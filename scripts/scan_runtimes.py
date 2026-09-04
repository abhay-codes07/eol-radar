"""Surface 1 of 5: language runtimes pinned by the repository itself.

Reads version-pin files (.nvmrc, .python-version, .tool-versions, go.mod,
engines, ...) and reports which runtime line each one selects. Filesystem only.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as c
import eol_data as ed

SURFACE = "runtimes"

# One-line pin files: filename -> endoflife.date product.
PIN_FILES = {
    ".nvmrc": "nodejs",
    ".node-version": "nodejs",
    ".python-version": "python",
    ".ruby-version": "ruby",
    ".java-version": "eclipse-temurin",
    ".php-version": "php",
    ".go-version": "go",
    ".terraform-version": "terraform",
    ".crystal-version": "crystal",
    ".dotnet-version": "dotnet",
}

INTEREST = set(PIN_FILES) | {
    ".tool-versions", "mise.toml", ".mise.toml", "package.json",
    "pyproject.toml", "runtime.txt", "go.mod", "gemfile", "global.json",
}


def _pin_file(root, path, product, subjects):
    text = c.read_text(path)
    where_file = c.rel(root, path)
    for number, line in enumerate(text.splitlines(), start=1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        version = ed.clean_version(raw)
        if not version:
            continue
        subjects.append(c.subject(
            "runtime",
            product + " " + version,
            where_file + ":" + str(number),
            raw,
            c.eol_lookup(product, version),
        ))
        break  # the first non-comment line is the pin


def _tool_versions(root, path, subjects):
    """asdf / mise .tool-versions: 'nodejs 20.11.1'."""
    text = c.read_text(path)
    where_file = c.rel(root, path)
    for number, line in enumerate(text.splitlines(), start=1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split()
        if len(parts) < 2:
            continue
        product = ed.TOOL_PRODUCTS.get(parts[0].lower())
        if not product:
            continue
        version = ed.clean_version(parts[1])
        if not version:
            continue
        subjects.append(c.subject(
            "runtime",
            product + " " + version,
            where_file + ":" + str(number),
            raw,
            c.eol_lookup(product, version),
        ))


def _mise_toml(root, path, subjects):
    """mise.toml [tools] node = "20"."""
    text = c.read_text(path)
    where_file = c.rel(root, path)
    in_tools = False
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("["):
            in_tools = stripped.lower().startswith("[tools")
            continue
        if not in_tools or "=" not in stripped or stripped.startswith("#"):
            continue
        key, value = stripped.split("=", 1)
        product = ed.TOOL_PRODUCTS.get(key.strip().strip('"').lower())
        if not product:
            continue
        version = ed.clean_version(value)
        if not version:
            continue
        subjects.append(c.subject(
            "runtime",
            product + " " + version,
            where_file + ":" + str(number),
            stripped,
            c.eol_lookup(product, version),
        ))


def _line_of(text, needle):
    for number, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return number
    return 1


def _package_json(root, path, subjects):
    data = c.read_json(path)
    if not isinstance(data, dict):
        return
    engines = data.get("engines")
    if not isinstance(engines, dict):
        return
    text = c.read_text(path)
    where_file = c.rel(root, path)
    for key, product in (("node", "nodejs"), ("npm", "npm")):
        raw = engines.get(key)
        if not raw or product == "npm":
            continue
        version = ed.clean_version(raw)
        if not version:
            continue
        note = None
        if ed.is_range(raw):
            note = "range '" + str(raw) + "': checked against its lowest allowed release"
        subjects.append(c.subject(
            "runtime",
            product + " " + version + " (engines." + key + ")",
            where_file + ":" + str(_line_of(text, '"' + key + '"')),
            str(raw),
            c.eol_lookup(product, version),
            note=note,
        ))


def _pyproject(root, path, subjects):
    text = c.read_text(path)
    where_file = c.rel(root, path)
    match = re.search(r'^\s*requires-python\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if not match:
        return
    raw = match.group(1)
    version = ed.clean_version(raw)
    if not version:
        return
    subjects.append(c.subject(
        "runtime",
        "python " + version + " (requires-python)",
        where_file + ":" + str(_line_of(text, "requires-python")),
        raw,
        c.eol_lookup("python", version),
        note="floor of the declared range" if ed.is_range(raw) else None,
    ))


def _runtime_txt(root, path, subjects):
    """Heroku-style runtime.txt: 'python-3.9.18'."""
    text = c.read_text(path).strip()
    if not text:
        return
    first = text.splitlines()[0].strip()
    lowered = first.lower()
    product = None
    for prefix, mapped in (("python", "python"), ("nodejs", "nodejs"),
                           ("node", "nodejs"), ("ruby", "ruby"), ("php", "php")):
        if lowered.startswith(prefix):
            product = mapped
            break
    if not product:
        return
    version = ed.clean_version(first)
    if not version:
        return
    subjects.append(c.subject(
        "runtime",
        product + " " + version + " (runtime.txt)",
        c.rel(root, path) + ":1",
        first,
        c.eol_lookup(product, version),
    ))


def _go_mod(root, path, subjects):
    text = c.read_text(path)
    match = re.search(r"^\s*go\s+(\d+\.\d+(?:\.\d+)?)\s*$", text, re.M)
    if not match:
        return
    subjects.append(c.subject(
        "runtime",
        "go " + match.group(1),
        c.rel(root, path) + ":" + str(_line_of(text, match.group(0).strip())),
        match.group(0).strip(),
        c.eol_lookup("go", match.group(1)),
    ))


def _gemfile(root, path, subjects):
    text = c.read_text(path)
    match = re.search(r'^\s*ruby\s+["\']([^"\']+)["\']', text, re.M)
    if not match:
        return
    version = ed.clean_version(match.group(1))
    if not version:
        return
    subjects.append(c.subject(
        "runtime",
        "ruby " + version,
        c.rel(root, path) + ":" + str(_line_of(text, "ruby")),
        match.group(1),
        c.eol_lookup("ruby", version),
    ))


def _global_json(root, path, subjects):
    data = c.read_json(path)
    if not isinstance(data, dict):
        return
    sdk = data.get("sdk")
    if not isinstance(sdk, dict):
        return
    version = ed.clean_version(sdk.get("version"))
    if not version:
        return
    subjects.append(c.subject(
        "runtime",
        "dotnet SDK " + version,
        c.rel(root, path) + ":" + str(_line_of(c.read_text(path), "version")),
        str(sdk.get("version")),
        c.eol_lookup("dotnet", version),
    ))


def scan(root, max_depth=8):
    subjects = []
    files = c.find_files(root, lambda base, _p: base.lower() in INTEREST, max_depth)
    for path in files:
        base = os.path.basename(path).lower()
        if base in PIN_FILES:
            _pin_file(root, path, PIN_FILES[base], subjects)
        elif base == ".tool-versions":
            _tool_versions(root, path, subjects)
        elif base in ("mise.toml", ".mise.toml"):
            _mise_toml(root, path, subjects)
        elif base == "package.json":
            _package_json(root, path, subjects)
        elif base == "pyproject.toml":
            _pyproject(root, path, subjects)
        elif base == "runtime.txt":
            _runtime_txt(root, path, subjects)
        elif base == "go.mod":
            _go_mod(root, path, subjects)
        elif base == "gemfile":
            _gemfile(root, path, subjects)
        elif base == "global.json":
            _global_json(root, path, subjects)
    return subjects, len(files)


def main(argv):
    root = c.check_root(c.arg_value(argv, "--root", "."))
    depth = int(c.arg_value(argv, "--depth", "8"))
    subjects, scanned = scan(root, depth)
    warning = None if subjects else "no runtime pin files found (.nvmrc, .python-version, engines, go.mod, ...)"
    c.emit(c.ok(SURFACE, subjects, warning, scanned))


if __name__ == "__main__":
    main(sys.argv[1:])
