"""Surface 5 of 5: dependencies that are deprecated, abandoned, or on an
end-of-life major line.

Reads lockfiles and manifests for concrete name/version pairs. Direct
dependencies are collected first so the query budget is spent where a human
can actually act. Framework packages that endoflife.date tracks by major line
get a second, date-bearing lookup.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as c
import eol_data as ed

SURFACE = "packages"

_YARN_ENTRY = re.compile(r'^"?(?P<spec>[^"\s][^:]*?)"?:\s*$')
_YARN_VERSION = re.compile(r'^\s+version:?\s+"?(?P<version>[^"\s]+)"?\s*$')
_PNPM_KEY = re.compile(r"^\s{2}/?(?P<name>(?:@[^/@\s]+/)?[^/@\s]+)[/@](?P<version>\d[^:(\s]*)")
_REQ_PIN = re.compile(r"^\s*(?P<name>[A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*==\s*(?P<version>[^\s;#]+)")
_GO_REQUIRE = re.compile(r"^\s*(?P<name>[^\s]+)\s+v(?P<version>[0-9][^\s/]*)")
_TOML_NAME = re.compile(r'^\s*name\s*=\s*"([^"]+)"')
_TOML_VERSION = re.compile(r'^\s*version\s*=\s*"([^"]+)"')


class Collector(object):
    def __init__(self):
        self.items = []
        self.seen = set()
        self.ranges = []

    def add(self, system, name, version, where, direct):
        if not name or not version:
            return
        version = version.strip().lstrip("v")
        key = (system, name.lower(), version)
        if key in self.seen:
            return
        self.seen.add(key)
        self.items.append({
            "system": system, "name": name, "version": version,
            "where": where, "direct": direct,
        })

    def add_range(self, system, name, floor, where, raw):
        """A manifest range such as ^20 names no real version.

        Querying its floor as if it were published produces a false 'not
        found'. It is kept aside; the lockfile normally supplies the concrete
        version, and if none does it is reported as unpinned rather than missing.
        """
        self.ranges.append({"system": system, "name": name, "version": floor,
                            "where": where, "raw": raw})

    def concrete_names(self):
        return set((item["system"], item["name"].lower()) for item in self.items)


def _package_json(root, path, out):
    data = c.read_json(path)
    if not isinstance(data, dict):
        return
    text = c.read_text(path)
    where_file = c.rel(root, path)
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        for name, raw in block.items():
            if not isinstance(raw, str) or raw.startswith(("workspace:", "file:", "link:", "git", "http")):
                continue
            line = 1
            for number, candidate in enumerate(text.splitlines(), start=1):
                if '"' + name + '"' in candidate:
                    line = number
                    break
            where = where_file + ":" + str(line)
            if not ed.is_range(raw):
                # An exact pin is queried exactly as written, prerelease and all:
                # 1.0.0-alpha.5 is a published version and 1.0.0 may not be.
                exact = raw.strip().lstrip("v=")
                if exact:
                    out.add("npm", name, exact, where, True)
                continue
            floor = ed.clean_version(raw)
            if not floor:
                continue
            # "^20" and "~4.18" name no real release, so they are kept aside.
            # "^18.2.0" has a real floor, and checking that floor is useful: it
            # says whether the oldest version the range admits is deprecated.
            if floor.count(".") < 2:
                out.add_range("npm", name, floor, where, raw)
            else:
                out.add("npm", name, floor, where, True)


def _package_lock(root, path, out):
    data = c.read_json(path)
    if not isinstance(data, dict):
        return
    where = c.rel(root, path)
    packages = data.get("packages")
    if isinstance(packages, dict):
        for key, entry in packages.items():
            if not key or not isinstance(entry, dict):
                continue
            name = entry.get("name") or key.split("node_modules/")[-1]
            version = entry.get("version")
            if name and version:
                out.add("npm", name, version, where, False)
        return
    dependencies = data.get("dependencies")
    if isinstance(dependencies, dict):
        stack = [(name, entry) for name, entry in dependencies.items()]
        while stack:
            name, entry = stack.pop()
            if not isinstance(entry, dict):
                continue
            if entry.get("version"):
                out.add("npm", name, entry["version"], where, False)
            nested = entry.get("dependencies")
            if isinstance(nested, dict):
                stack.extend(nested.items())


def _yarn_lock(root, path, out):
    where = c.rel(root, path)
    pending = None
    for line in c.read_text(path).splitlines():
        if line.startswith("#") or not line.strip():
            continue
        if not line.startswith(" "):
            match = _YARN_ENTRY.match(line.strip())
            if match:
                spec = match.group("spec").split(",")[0].strip().strip('"')
                at = spec.rfind("@")
                pending = spec[:at] if at > 0 else spec
            continue
        version = _YARN_VERSION.match(line)
        if version and pending:
            out.add("npm", pending, version.group("version"), where, False)
            pending = None


def _pnpm_lock(root, path, out):
    where = c.rel(root, path)
    for line in c.read_text(path).splitlines():
        match = _PNPM_KEY.match(line)
        if match:
            out.add("npm", match.group("name"), match.group("version"), where, False)


def _requirements(root, path, out):
    where_file = c.rel(root, path)
    for number, line in enumerate(c.read_text(path).splitlines(), start=1):
        if line.strip().startswith("#"):
            continue
        match = _REQ_PIN.match(line)
        if match:
            out.add("pypi", match.group("name"), match.group("version"),
                    where_file + ":" + str(number), True)


def _toml_packages(root, path, out, system):
    """poetry.lock, uv.lock and Cargo.lock all use [[package]] blocks."""
    where = c.rel(root, path)
    name = None
    for line in c.read_text(path).splitlines():
        if line.strip().startswith("[[package]]"):
            name = None
            continue
        if name is None:
            match = _TOML_NAME.match(line)
            if match:
                name = match.group(1)
                continue
        else:
            match = _TOML_VERSION.match(line)
            if match:
                out.add(system, name, match.group(1), where, False)
                name = None


def _pipfile_lock(root, path, out):
    data = c.read_json(path)
    if not isinstance(data, dict):
        return
    where = c.rel(root, path)
    for section in ("default", "develop"):
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        for name, entry in block.items():
            if isinstance(entry, dict):
                # Pipfile.lock pins as "==1.2.3rc1"; keep everything after the
                # operator so a prerelease is queried as itself.
                version = str(entry.get("version") or "").strip().lstrip("=").strip()
                if version:
                    out.add("pypi", name, version, where, section == "default")


def _go_mod(root, path, out):
    where_file = c.rel(root, path)
    inside = False
    for number, line in enumerate(c.read_text(path).splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("require ("):
            inside = True
            continue
        if inside and stripped == ")":
            inside = False
            continue
        candidate = stripped[len("require "):] if stripped.startswith("require ") else (stripped if inside else "")
        if not candidate or candidate.startswith("//"):
            continue
        match = _GO_REQUIRE.match(candidate)
        if match:
            out.add("go", match.group("name"), match.group("version"),
                    where_file + ":" + str(number), not inside)


def _gemfile_lock(root, path, out):
    where = c.rel(root, path)
    in_specs = False
    for line in c.read_text(path).splitlines():
        if line.strip() == "specs:":
            in_specs = True
            continue
        if in_specs and line and not line.startswith(" "):
            in_specs = False
        if not in_specs:
            continue
        match = re.match(r"^\s{4}([A-Za-z0-9._-]+) \(([^)]+)\)\s*$", line)
        if match:
            # The lockfile version is exact, prerelease and platform suffix
            # included: 1.0.0.beta1 is a real gem, 1.0.0 may not be.
            version = match.group(2).strip().split("-")[0]
            if version:
                out.add("rubygems", match.group(1), version, where, False)


def _composer_lock(root, path, out):
    data = c.read_json(path)
    if not isinstance(data, dict):
        return
    where = c.rel(root, path)
    for section in ("packages", "packages-dev"):
        block = data.get(section)
        if not isinstance(block, list):
            continue
        for entry in block:
            if isinstance(entry, dict):
                # composer.lock records exact tags such as v1.2.3 or 2.0.0-RC1;
                # only the leading v is decoration.
                version = str(entry.get("version") or "").strip().lstrip("v")
                if version:
                    out.add("packagist", entry.get("name"), version, where, section == "packages")


READERS = (
    ("package.json", _package_json),
    ("package-lock.json", _package_lock),
    ("yarn.lock", _yarn_lock),
    ("pnpm-lock.yaml", _pnpm_lock),
    ("pipfile.lock", _pipfile_lock),
    ("cargo.lock", lambda r, p, o: _toml_packages(r, p, o, "cargo")),
    ("poetry.lock", lambda r, p, o: _toml_packages(r, p, o, "pypi")),
    ("uv.lock", lambda r, p, o: _toml_packages(r, p, o, "pypi")),
    ("go.mod", _go_mod),
    ("gemfile.lock", _gemfile_lock),
    ("composer.lock", _composer_lock),
)

NAMES = set(name for name, _ in READERS)

# deps.dev v3 answers for these ecosystems only.
QUERYABLE = {"npm", "pypi", "cargo", "go", "maven", "nuget", "rubygems"}


def _is_target(base, _path):
    lowered = base.lower()
    return lowered in NAMES or (lowered.startswith("requirements") and lowered.endswith(".txt"))


# Manifests name direct dependencies, so they are read before lockfiles: the
# first reader to claim a name/version decides where the finding points, and a
# line in package.json is more use to a human than a line in a lockfile.
MANIFESTS = ("package.json", "go.mod")


def _manifest_first(root):
    def key(path):
        base = os.path.basename(path).lower()
        direct = base in MANIFESTS or (base.startswith("requirements") and base.endswith(".txt"))
        return (0 if direct else 1, c.rel(root, path))
    return key


def scan(root, max_depth=8, max_packages=300):
    out = Collector()
    files = sorted(c.find_files(root, _is_target, max_depth), key=_manifest_first(root))
    for path in files:
        base = os.path.basename(path).lower()
        if base.startswith("requirements") and base.endswith(".txt"):
            _requirements(root, path, out)
            continue
        for name, reader in READERS:
            if base == name:
                try:
                    reader(root, path, out)
                except Exception as error:            # a malformed lockfile must not kill the surface
                    sys.stderr.write("eol-radar: skipped " + c.rel(root, path) + ": " + str(error) + "\n")
                break

    # Direct dependencies first, then a stable order, then the cap.
    ordered = sorted(out.items, key=lambda item: (not item["direct"], item["system"], item["name"]))
    truncated = max(0, len(ordered) - max_packages)
    ordered = ordered[:max_packages]

    subjects = []
    for item in ordered:
        system, name, version = item["system"], item["name"], item["version"]
        label = name + "@" + version
        if system in QUERYABLE:
            lookup = c.package_lookup(system, name, version)
        else:
            lookup = c.no_lookup("ecosystem '" + system + "' is not covered by deps.dev")
        subjects.append(c.subject(
            "package", label, item["where"], label, lookup,
            extra={"system": system, "direct": item["direct"]},
        ))
        product = ed.FRAMEWORK_PRODUCTS.get((system, name.lower()))
        if product:
            subjects.append(c.subject(
                "framework", product + " " + version, item["where"], label,
                c.eol_lookup(product, version),
                fix="upgrade to a supported " + product + " release line",
                extra={"system": system},
            ))

    # Ranges the lockfile did not pin. They cost no query budget, they are
    # reported as unpinned rather than "not found", and a framework range still
    # gets its lifecycle check because the major line is what that answers.
    concrete = out.concrete_names()
    for item in out.ranges:
        system, name = item["system"], item["name"]
        if (system, name.lower()) in concrete:
            continue
        label = name + "@" + item["raw"]
        subjects.append(c.subject(
            "package", label, item["where"], item["raw"],
            c.no_lookup("range '" + item["raw"] + "' is not pinned by a lockfile; "
                        "nothing concrete to check"),
            extra={"system": system, "direct": True, "range": True},
        ))
        product = ed.FRAMEWORK_PRODUCTS.get((system, name.lower()))
        if product:
            subjects.append(c.subject(
                "framework", product + " " + item["version"], item["where"], label,
                c.eol_lookup(product, item["version"]),
                note="floor of the declared range",
                fix="upgrade to a supported " + product + " release line",
                extra={"system": system},
            ))
    return subjects, len(files), truncated


def main(argv):
    root = c.check_root(c.arg_value(argv, "--root", "."), in_play=c.arg_flag(argv, "--in-play"))
    depth = int(c.arg_value(argv, "--depth", "8"))
    try:
        cap = int(c.arg_value(argv, "--max", "300"))
    except ValueError:
        c.fail("--max must be a whole number")
    if cap < 1 or cap > 5000:
        c.fail("--max must be between 1 and 5000")
    subjects, scanned, truncated = scan(root, depth, cap)
    warning = None
    if not scanned:
        warning = "no lockfiles or manifests found"
    elif truncated:
        warning = "stopped at " + str(cap) + " packages; " + str(truncated) + " more were not queried (raise max_packages)"
    result = c.ok(SURFACE, subjects, warning, scanned)
    result["truncated"] = truncated
    c.emit(result)


if __name__ == "__main__":
    main(sys.argv[1:])
