#!/usr/bin/env python3
"""Assemble the rote Play package for EOL Radar from this repository.

    python3 play/pack.py <package-dir>

The Play ships its own scanner: the package a stranger pulls contains every
line of code the steps run, so it can be read before it is run and nothing is
fetched at run time. This script is the only way that package is built:

  play/main.ts            -> <package-dir>/main.ts
  play/deps.toml          -> <package-dir>/deps.toml
  scripts/<the modules the steps invoke and import>
                          -> <package-dir>/resources/scripts/
  data/enforcement.json   -> <package-dir>/resources/data/enforcement.json

  play/resources/presentation-fixtures/, play/resources/cases/
                          -> <package-dir>/resources/ (the positive fixtures
                             `play audit fixtures` recorded from a verified
                             run, and the negative cases `play audit rehearse`
                             replays against the presentation)

After copying, every `@resource{...}` token and every declared presentation
fixture in the frontmatter is checked against the files that now exist, and
every packaged script is parsed as Python 3.8 so the declared floor stays true.
"""
import ast
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# The closure of the modules the steps run: five scanners, resolve, join, and
# the modules those import. The local runner (eol_radar.py) and the account
# view (aggregate.py) are CLI conveniences and are not part of the Play.
PACKAGE_SCRIPTS = [
    "common.py", "eol_data.py",
    "scan_runtimes.py", "scan_containers.py", "scan_ci.py", "scan_cloud.py", "scan_packages.py",
    "resolve.py", "join.py", "gate.py", "migrate.py", "ownership.py",
]
PACKAGE_DATA = ["enforcement.json"]
EVIDENCE_DIRS = ["presentation-fixtures", "cases"]
FLOOR = (3, 8)


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def frontmatter(main_ts):
    text = read_text(main_ts)
    start = text.index("@rote-frontmatter")
    end = text.index("*/", start)
    return "\n".join(re.sub(r"^\s*\*\s?", "", line) for line in text[start:end].splitlines())


def resource_tokens(main_ts):
    return sorted(set(re.findall(r"@resource\{([^}]+)\}", frontmatter(main_ts))))


def fixture_paths(main_ts):
    return sorted(set(re.findall(r"^\s*\w+:\s+(resources/presentation-fixtures/\S+)$",
                                 frontmatter(main_ts), re.M)))


def pack(package_dir):
    package_dir = os.path.abspath(package_dir)
    os.makedirs(package_dir, exist_ok=True)
    for name in ("main.ts", "deps.toml"):
        shutil.copyfile(os.path.join(HERE, name), os.path.join(package_dir, name))

    scripts_dir = os.path.join(package_dir, "resources", "scripts")
    data_dir = os.path.join(package_dir, "resources", "data")
    for directory in (scripts_dir, data_dir):
        if os.path.isdir(directory):
            shutil.rmtree(directory)
        os.makedirs(directory)
    written = []
    for name in PACKAGE_SCRIPTS:
        shutil.copyfile(os.path.join(REPO, "scripts", name), os.path.join(scripts_dir, name))
        written.append("resources/scripts/" + name)
    for name in PACKAGE_DATA:
        shutil.copyfile(os.path.join(REPO, "data", name), os.path.join(data_dir, name))
        written.append("resources/data/" + name)
    for evidence in EVIDENCE_DIRS:
        source = os.path.join(HERE, "resources", evidence)
        target = os.path.join(package_dir, "resources", evidence)
        if os.path.isdir(target):
            shutil.rmtree(target)
        if os.path.isdir(source):
            shutil.copytree(source, target)
            for base, _dirs, files in os.walk(source):
                for name in sorted(files):
                    written.append(os.path.relpath(os.path.join(base, name), HERE).replace(os.sep, "/"))

    problems = []
    main_ts = os.path.join(package_dir, "main.ts")
    for token in resource_tokens(main_ts):
        if not os.path.isfile(os.path.join(package_dir, "resources", token)):
            problems.append("frontmatter names @resource{%s} but resources/%s is not in the package" % (token, token))
    for path in fixture_paths(main_ts):
        if not os.path.isfile(os.path.join(package_dir, path)):
            problems.append("frontmatter declares the fixture %s but it is not in the package" % path)
    for name in PACKAGE_SCRIPTS:
        path = os.path.join(scripts_dir, name)
        try:
            ast.parse(read_text(path), path, feature_version=FLOOR)
        except SyntaxError as exc:
            problems.append("%s line %s is not Python %d.%d: %s" % (name, exc.lineno, FLOOR[0], FLOOR[1], exc.msg))
    return written, problems


def main(argv):
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        sys.stderr.write(__doc__)
        return 2
    written, problems = pack(argv[1])
    for path in written:
        print("packed  " + path)
    for problem in problems:
        print("PROBLEM " + problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
