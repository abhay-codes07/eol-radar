"""Mapping layer: names as they appear in a repository -> endoflife.date products.

The alias tables below are best guesses. They are safe to be wrong: resolve.py
treats an unknown product as UNKNOWN rather than inventing a date, so a bad
guess costs a row of "not tracked", never a false deadline.
"""

import re

# Container image name (registry and namespace stripped) -> endoflife.date product.
IMAGE_PRODUCTS = {
    "node": "nodejs",
    "nodejs": "nodejs",
    "python": "python",
    "ruby": "ruby",
    "golang": "go",
    "go": "go",
    "php": "php",
    "rust": "rust",
    "eclipse-temurin": "eclipse-temurin",
    "amazoncorretto": "amazon-corretto",
    "amazon-corretto": "amazon-corretto",
    "alpine": "alpine-linux",
    "debian": "debian",
    "ubuntu": "ubuntu",
    "amazonlinux": "amazon-linux",
    "centos": "centos",
    "fedora": "fedora",
    "rockylinux": "rocky-linux",
    "almalinux": "almalinux",
    "opensuse": "opensuse",
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "mysql": "mysql",
    "mariadb": "mariadb",
    "mongo": "mongodb",
    "mongodb": "mongodb",
    "redis": "redis",
    "valkey": "valkey",
    "memcached": "memcached",
    "nginx": "nginx",
    "httpd": "apache-http-server",
    "haproxy": "haproxy",
    "traefik": "traefik",
    "elasticsearch": "elasticsearch",
    "opensearch": "opensearch",
    "logstash": "logstash",
    "kibana": "kibana",
    "rabbitmq": "rabbitmq",
    "kafka": "apache-kafka",
    "consul": "consul",
    "nomad": "nomad",
    "terraform": "terraform",
    "jenkins": "jenkins",
    "grafana": "grafana",
    "influxdb": "influxdb",
    "neo4j": "neo4j",
    "solr": "solr",
    "tomcat": "tomcat",
    "wordpress": "wordpress",
    "drupal": "drupal",
    "gitlab-ce": "gitlab",
    "gitlab-ee": "gitlab",
    "sdk": "dotnet",
    "aspnet": "dotnet",
    "runtime": "dotnet",
}

# Runtime pin files and .tool-versions / mise keys -> endoflife.date product.
TOOL_PRODUCTS = {
    "node": "nodejs",
    "nodejs": "nodejs",
    "python": "python",
    "ruby": "ruby",
    "go": "go",
    "golang": "go",
    "java": "eclipse-temurin",
    "temurin": "eclipse-temurin",
    "php": "php",
    "rust": "rust",
    "elixir": "elixir",
    "erlang": "erlang",
    "dotnet": "dotnet",
    "dotnet-core": "dotnet",
    "terraform": "terraform",
    "kubectl": "kubernetes",
    "postgres": "postgresql",
    "deno": "deno",
    "bun": "bun",
}

# Framework / library package names that endoflife.date tracks by major line.
FRAMEWORK_PRODUCTS = {
    ("npm", "react"): "react",
    ("npm", "react-dom"): "react",
    ("npm", "next"): "nextjs",
    ("npm", "nuxt"): "nuxt",
    ("npm", "vue"): "vue",
    ("npm", "@angular/core"): "angular",
    ("npm", "electron"): "electron",
    ("npm", "express"): "express",
    ("npm", "eslint"): "eslint",
    ("npm", "bootstrap"): "bootstrap",
    ("npm", "jquery"): "jquery",
    ("pypi", "django"): "django",
    ("pypi", "numpy"): "numpy",
    ("rubygems", "rails"): "rails",
    ("maven", "org.springframework.boot"): "spring-boot",
    ("packagist", "laravel/framework"): "laravel",
    ("packagist", "symfony/symfony"): "symfony",
}

# Debian and Ubuntu release codenames used as image tags.
CODENAMES = {
    "debian": {
        "buzz": "1.1", "rex": "1.2", "bo": "1.3", "hamm": "2.0", "slink": "2.1",
        "potato": "2.2", "woody": "3.0", "sarge": "3.1", "etch": "4",
        "lenny": "5", "squeeze": "6", "wheezy": "7", "jessie": "8",
        "stretch": "9", "buster": "10", "bullseye": "11", "bookworm": "12",
        "trixie": "13", "forky": "14",
    },
    "ubuntu": {
        "trusty": "14.04", "xenial": "16.04", "bionic": "18.04",
        "focal": "20.04", "jammy": "22.04", "noble": "24.04",
        "oracular": "24.10", "plucky": "25.04", "questing": "25.10",
        "resolute": "26.04",
    },
}

FLOATING_TAGS = {"latest", "lts", "current", "stable", "alpine", "slim", "edge",
                 "main", "master", "nightly", "dev", "devel", "bookworm-slim"}

# Docker tag suffixes that describe the OS layer, not the product version.
_VARIANT = re.compile(
    r"-(alpine|slim|bullseye|bookworm|buster|trixie|jammy|focal|noble|bionic|"
    r"windowsservercore|nanoserver|ubi\d*|distroless|otel|fpm|apache|cli|"
    r"perl|scm|dind|jdk|jre|headless|full|onbuild)(\d[\d.]*)?$"
)

_VERSIONISH = re.compile(r"^v?\d+(\.\d+)*$")


def split_image(reference):
    """Split 'ghcr.io/org/node:20-alpine' into ('node', '20-alpine', 'ghcr.io/org')."""
    reference = reference.strip()
    if not reference:
        return None, None, None
    if "@" in reference:                       # digest pin: name@sha256:...
        reference = reference.split("@", 1)[0]
    parts = reference.split("/")
    last = parts[-1]
    namespace = "/".join(parts[:-1])
    if ":" in last:
        name, tag = last.rsplit(":", 1)
    else:
        name, tag = last, ""
    return name.lower(), tag.strip(), namespace.lower()


def image_product(name, namespace=""):
    """Best-effort endoflife.date product for a container image name."""
    if not name:
        return None
    if "dotnet" in (namespace or ""):
        return "dotnet"
    return IMAGE_PRODUCTS.get(name)


_DISTROLESS = re.compile(r"^[a-z0-9]+-debian(\d+)$")


def distroless_debian(name, namespace):
    """gcr.io/distroless/base-debian12 carries its Debian release in the name.

    The tag on those images is a digest or 'nonroot', never a version, so the
    usual tag parsing finds nothing. Returns the Debian cycle or None.
    """
    if "distroless" not in (namespace or ""):
        return None
    match = _DISTROLESS.match(name or "")
    return match.group(1) if match else None


def cycle_from_tag(product, tag):
    """Turn a Docker tag into something matchable against endoflife.date cycles.

    Returns (cycle, note). cycle is None when the tag does not pin a version.
    """
    if not tag:
        return None, "no tag: resolves to :latest, so the version is whatever is current"
    lowered = tag.lower()
    codemap = CODENAMES.get(product or "", {})
    base = _VARIANT.sub("", lowered)
    if base in codemap:
        return codemap[base], "codename tag " + base
    if lowered in codemap:
        return codemap[lowered], "codename tag " + lowered
    if base in FLOATING_TAGS or not base:
        return None, "floating tag '" + tag + "': not pinned to a release line"
    base = base.lstrip("v")
    head = base.split("-")[0]
    if _VERSIONISH.match(head):
        return head, None
    return None, "tag '" + tag + "' does not name a version"


def clean_version(raw):
    """Reduce a pin like '>=20.11.1 <21' or 'v3.9.18' to a plain version."""
    if raw is None:
        return None
    text = str(raw).strip().strip('"').strip("'")
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)*)", text)
    if not match:
        return None
    return match.group(1)


def is_range(raw):
    """True when a pin admits several versions (^, ~, >=, *, 1.x).

    The x wildcard only counts as a whole version component, so the letter in
    1.0.0-next.3 does not turn an exact prerelease pin into a range.
    """
    text = str(raw or "").strip()
    return bool(re.search(r"[\^~*><|]|\s-\s|(?:^|\.)[xX](?:\.|$)", text))


def match_cycle(version, cycle_names):
    """Pick the endoflife.date cycle a concrete version belongs to.

    Exact name first, then the longest dotted prefix, so 3.9.18 finds cycle
    '3.9' and 20.11.1 finds cycle '20'. Unknown versions return None and are
    reported as UNKNOWN instead of being forced into a neighbouring cycle.
    """
    if version is None:
        return None
    version = str(version).strip().lstrip("v")
    if not version:
        return None
    names = [str(name) for name in cycle_names]
    if version in names:
        return version
    best = None
    for name in names:
        if version == name or version.startswith(name + "."):
            if best is None or len(name) > len(best):
                best = name
    if best:
        return best
    # Fall back to the leading major so 20.11.1 still finds a cycle called 20.
    head = version.split(".")[0]
    if head in names:
        return head
    # A bare major such as redis:7 or python:3 floats to the newest release of
    # that line, so it is evaluated as the newest cycle it prefixes: 7 -> 7.4.
    parts = version.split(".")
    candidates = []
    for name in names:
        name_parts = name.split(".")
        if len(name_parts) > len(parts) and name_parts[:len(parts)] == parts:
            try:
                candidates.append((tuple(int(p) for p in name_parts), name))
            except ValueError:
                continue
    if candidates:
        return max(candidates)[1]
    return None


def normalize_action_ref(uses):
    """Parse a workflow 'uses:' value into its parts.

    Returns (owner, repo, subpath, ref) or None when the reference is local.
    """
    value = (uses or "").strip()
    if not value or value.startswith("./") or value.startswith("../"):
        return None
    if value.startswith("docker://"):
        return None
    if "@" not in value:
        return None
    target, ref = value.rsplit("@", 1)
    pieces = target.split("/")
    if len(pieces) < 2:
        return None
    owner, repo = pieces[0], pieces[1]
    subpath = "/".join(pieces[2:])
    return owner, repo, subpath, ref.strip()


# Action runtimes that stop starting once the runners drop Node 20.
DEAD_ACTION_RUNTIMES = {"node12", "node16", "node20"}
LIVE_ACTION_RUNTIMES = {"node24", "docker", "composite"}
