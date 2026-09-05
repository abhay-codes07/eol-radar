"""Surface 2 of 5: container base images.

Reads Dockerfiles, compose files and devcontainers, and reports which release
line every base image tag selects. Build stages and scratch are ignored, and
ARG defaults are substituted so FROM node:${NODE_VERSION} still resolves.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as c
import eol_data as ed

SURFACE = "containers"

_FROM = re.compile(r"^\s*FROM\s+(?P<rest>.+?)\s*$", re.I)
_ARG = re.compile(r"^\s*ARG\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>\S+)", re.I)
_VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}?")


def _is_dockerfile(base, _path):
    lowered = base.lower()
    return (lowered == "dockerfile" or lowered.startswith("dockerfile.")
            or lowered.endswith(".dockerfile") or lowered == "containerfile")


def _is_compose(base, _path):
    lowered = base.lower()
    if not (lowered.endswith(".yml") or lowered.endswith(".yaml")):
        return False
    stem = lowered.rsplit(".", 1)[0]
    return stem.startswith("docker-compose") or stem.startswith("compose")


def _substitute(reference, args):
    def replace(match):
        name, default = match.group(1), match.group(2)
        return args.get(name, default if default is not None else match.group(0))
    return _VAR.sub(replace, reference)


def _add_image(subjects, reference, where, origin, raw_line):
    name, tag, namespace = ed.split_image(reference)
    if not name:
        return
    if "$" in reference:
        subjects.append(c.subject(
            "image", reference, where, raw_line,
            c.no_lookup("image tag is built from an unresolved variable"),
            note="could not resolve " + reference + " without building",
            extra={"origin": origin},
        ))
        return
    debian = ed.distroless_debian(name, namespace)
    if debian:
        subjects.append(c.subject(
            "image", reference, where, raw_line, c.eol_lookup("debian", debian),
            note="distroless image; the Debian release is in the image name",
            fix="move to a distroless image built on a maintained Debian release",
            extra={"origin": origin},
        ))
        return
    product = ed.image_product(name, namespace)
    if not product:
        subjects.append(c.subject(
            "image", reference, where, raw_line,
            c.no_lookup("image '" + name + "' has no known lifecycle feed"),
            note="not a tracked base image; check the publisher's own schedule",
            extra={"origin": origin},
        ))
        return
    cycle, note = ed.cycle_from_tag(product, tag)
    if not cycle:
        subjects.append(c.subject(
            "image", reference, where, raw_line,
            c.no_lookup(note or "tag does not pin a release"),
            note=note, extra={"origin": origin},
        ))
        return
    subjects.append(c.subject(
        "image", reference, where, raw_line,
        c.eol_lookup(product, cycle), note=note,
        fix="pin a maintained tag of " + name,
        extra={"origin": origin},
    ))

    # A tag like python:3.9-bookworm also pins a distro layer. Report it too.
    lowered = (tag or "").lower()
    for distro in ("debian", "ubuntu"):
        for codename, release in ed.CODENAMES[distro].items():
            if re.search(r"(^|-)" + codename + r"($|-)", lowered) and product != distro:
                subjects.append(c.subject(
                    "image", distro + " " + release + " (in " + reference + ")",
                    where, raw_line, c.eol_lookup(distro, release),
                    note="OS layer of the base image, from tag suffix '" + codename + "'",
                    extra={"origin": origin},
                ))
                return


def _dockerfile(root, path, subjects):
    text = c.read_text(path)
    where_file = c.rel(root, path)
    args = {}
    stages = set()
    for number, line in enumerate(text.splitlines(), start=1):
        arg = _ARG.match(line)
        if arg:
            args[arg.group("name")] = arg.group("value").strip('"').strip("'")
            continue
        match = _FROM.match(line)
        if not match:
            continue
        rest = match.group("rest")
        tokens = [t for t in rest.split() if not t.lower().startswith("--platform=")]
        if not tokens:
            continue
        reference = tokens[0]
        if len(tokens) >= 3 and tokens[1].lower() == "as":
            stages.add(tokens[2].lower())
        if reference.lower() == "scratch" or reference.lower() in stages:
            continue
        _add_image(subjects, _substitute(reference, args),
                   where_file + ":" + str(number), "dockerfile", line.strip())


def _dotenv(path):
    """KEY=VALUE pairs from the .env file Compose reads next to the compose file."""
    values = {}
    for line in c.read_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        values[key.strip()] = c.unquote(value.strip())
    return values


def _compose(root, path, subjects):
    text = c.read_text(path)
    where_file = c.rel(root, path)
    # Compose substitutes ${VAR} from the .env beside the file, so resolving it
    # the same way turns "unresolved variable" into an actual image for most
    # repositories. Anything still unresolved is reported as such.
    env = _dotenv(os.path.join(os.path.dirname(path), ".env"))
    for number, _indent, key, value in c.yaml_pairs(text):
        if key != "image":
            continue
        reference = c.scalar(value)
        if reference:
            _add_image(subjects, _substitute(reference, env), where_file + ":" + str(number),
                       "compose", "image: " + reference)


def _devcontainer(root, path, subjects):
    data = c.read_json(path)
    if not isinstance(data, dict):
        return
    reference = data.get("image")
    if isinstance(reference, str) and reference:
        _add_image(subjects, reference, c.rel(root, path) + ":1",
                   "devcontainer", '"image": "' + reference + '"')


def scan(root, max_depth=8):
    subjects = []
    dockerfiles = c.find_files(root, _is_dockerfile, max_depth)
    composes = c.find_files(root, _is_compose, max_depth)
    devcontainers = c.find_files(root, c.name_in("devcontainer.json"), max_depth)
    for path in dockerfiles:
        _dockerfile(root, path, subjects)
    for path in composes:
        _compose(root, path, subjects)
    for path in devcontainers:
        _devcontainer(root, path, subjects)
    return subjects, len(dockerfiles) + len(composes) + len(devcontainers)


def main(argv):
    root = c.check_root(c.arg_value(argv, "--root", "."))
    depth = int(c.arg_value(argv, "--depth", "8"))
    subjects, scanned = scan(root, depth)
    warning = None if scanned else "no Dockerfile, compose or devcontainer files found"
    c.emit(c.ok(SURFACE, subjects, warning, scanned))


if __name__ == "__main__":
    main(sys.argv[1:])
