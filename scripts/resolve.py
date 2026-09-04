"""Turn the subjects found on disk into dated lifecycle facts.

This is the only step that touches the network, and it only reads public,
keyless endpoints:

  endoflife.date         release cycles, end-of-life and end-of-active-support
  raw.githubusercontent  an action's action.yml at the exact ref, for runs.using
  deps.dev (Google)      per-version isDeprecated and the upstream repository
  registry.npmjs.org     the npm deprecation message, as a fallback
  api.github.com         whether the upstream repository is archived (optional)

Every failure degrades to a labelled unknown. Nothing here writes anything,
and no credential is required: GITHUB_TOKEN is read if present purely to lift
the archived check from 60 requests an hour to 5,000.
"""

import json
import os
import sys
import time

try:
    from concurrent.futures import ThreadPoolExecutor
except ImportError:                                   # pragma: no cover
    ThreadPoolExecutor = None

from urllib import request as urlrequest
from urllib import parse as urlparse
from urllib import error as urlerror

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as c
import eol_data as ed

LIVE_RUNTIMES = ed.LIVE_ACTION_RUNTIMES
DEAD_RUNTIMES = ed.DEAD_ACTION_RUNTIMES

USER_AGENT = "eol-radar (+https://github.com/abhay-codes07/eol-radar)"
EOL_API = "https://endoflife.date/api/v1/products/"
DEPSDEV_API = "https://api.deps.dev/v3/systems/"
NPM_API = "https://registry.npmjs.org/"
GITHUB_API = "https://api.github.com/repos/"
RAW_GITHUB = "https://raw.githubusercontent.com/"

DEFAULT_TTL = 6 * 3600


def cache_dir():
    override = os.environ.get("EOL_RADAR_CACHE")
    if override:
        return override
    import tempfile
    return os.path.join(tempfile.gettempdir(), "eol-radar-cache")


class Http(object):
    """Small cached HTTP reader with a per-host courtesy delay."""

    def __init__(self, ttl=DEFAULT_TTL, use_cache=True, timeout=20):
        self.ttl = ttl
        self.use_cache = use_cache
        self.timeout = timeout
        self.directory = cache_dir()
        self.stats = {"hits": 0, "fetches": 0, "errors": 0}
        if self.use_cache:
            try:
                os.makedirs(self.directory)
            except OSError:
                pass

    def _cache_path(self, url):
        import hashlib
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:40]
        return os.path.join(self.directory, digest + ".json")

    def _read_cache(self, url):
        if not self.use_cache:
            return None
        path = self._cache_path(url)
        try:
            if time.time() - os.path.getmtime(path) > self.ttl:
                return None
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    def _write_cache(self, url, payload):
        if not self.use_cache:
            return
        try:
            with open(self._cache_path(url), "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
        except OSError:
            pass

    def get(self, url, headers=None, as_json=True):
        """Return (payload, error). error is None on success, a string otherwise."""
        cached = self._read_cache(url)
        if cached is not None:
            self.stats["hits"] += 1
            return cached.get("body"), cached.get("error")
        request = urlrequest.Request(url)
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Accept", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        body, error = None, None
        try:
            self.stats["fetches"] += 1
            with urlrequest.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
            body = json.loads(raw) if as_json else raw
        except urlerror.HTTPError as exc:
            error = "http " + str(exc.code)
            self.stats["errors"] += 1
        except (urlerror.URLError, ValueError, OSError) as exc:
            error = "unreachable: " + str(getattr(exc, "reason", exc))[:120]
            self.stats["errors"] += 1
        self._write_cache(url, {"body": body, "error": error})
        return body, error


# ---------------------------------------------------------------------------
# endoflife.date
# ---------------------------------------------------------------------------

def _releases(payload):
    if not isinstance(payload, dict):
        return []
    result = payload.get("result", payload)
    releases = result.get("releases") if isinstance(result, dict) else None
    return releases if isinstance(releases, list) else []


def fetch_product(http, product):
    payload, error = http.get(EOL_API + urlparse.quote(product, safe=""))
    if error:
        if error == "http 404":
            return {"known": False, "reason": "endoflife.date does not track '" + product + "'"}
        return {"known": False, "reason": "endoflife.date " + error, "degraded": True}
    releases = _releases(payload)
    if not releases:
        return {"known": False, "reason": "no release data for '" + product + "'"}
    cycles = {}
    for release in releases:
        if not isinstance(release, dict):
            continue
        name = release.get("name")
        if name is None:
            continue
        latest = release.get("latest")
        cycles[str(name)] = {
            "label": release.get("label"),
            "is_eol": bool(release.get("isEol")),
            "is_maintained": bool(release.get("isMaintained")),
            "is_lts": bool(release.get("isLts")),
            "release_date": release.get("releaseDate"),
            "eoas": release.get("eoasFrom"),
            "eol": release.get("eolFrom"),
            "eoes": release.get("eoesFrom"),
            "latest": (latest or {}).get("name") if isinstance(latest, dict) else latest,
        }
    # Releases arrive newest first. Recommend the newest maintained LTS where the
    # product has one: pointing somebody at an upcoming release that is not yet
    # LTS is worse advice than the release line they are already on.
    newest, newest_lts = None, None
    for release in releases:
        if not isinstance(release, dict) or not release.get("isMaintained"):
            continue
        if newest is None:
            newest = str(release.get("name"))
        if release.get("isLts") and newest_lts is None:
            newest_lts = str(release.get("name"))
    return {"known": True, "cycles": cycles,
            "newest_maintained": newest, "newest_lts": newest_lts}


# ---------------------------------------------------------------------------
# GitHub Actions: what runtime does this action really declare at this ref?
# ---------------------------------------------------------------------------

def find_action_upgrade(http, owner, repo, path, ref):
    """Find the nearest major release of an action that runs on a live runtime.

    Deliberately does not call the GitHub API: it just tries to read action.yml
    at the next few major tags on raw.githubusercontent, which is not subject to
    the 60-per-hour anonymous API budget. The lowest working major is returned,
    because the smallest upgrade that fixes the problem is the one people take.
    """
    import re as _re
    match = _re.match(r"^v?(\d+)(?:[.\d]*)$", (ref or "").strip())
    if not match:
        return None                      # a SHA or a moving branch: nothing to increment
    current = int(match.group(1))
    for major in range(current + 1, current + 4):
        for candidate in ("v%d" % major, "%d" % major):
            fact = fetch_action(http, owner, repo, path, candidate)
            if not fact.get("known"):
                continue
            if fact.get("using") in LIVE_RUNTIMES:
                return {"ref": candidate, "using": fact.get("using")}
            break                        # tag exists but is still old; try the next major
    return None


def fetch_action(http, owner, repo, path, ref):
    prefix = RAW_GITHUB + "/".join([owner, repo, ref])
    subdir = (path + "/") if path else ""
    for filename in ("action.yml", "action.yaml"):
        url = prefix + "/" + subdir + filename
        body, error = http.get(url, as_json=False)
        if error:
            continue
        using = None
        in_runs = False
        for _number, indent, key, value in c.yaml_pairs(body or ""):
            if indent == 0:
                in_runs = (key == "runs")
                continue
            if in_runs and key == "using":
                using = c.scalar(value).lower()
                break
        if using:
            return {"known": True, "using": using, "source": url}
        return {"known": False, "reason": "action.yml has no runs.using (reusable workflow?)", "source": url}
    return {"known": False, "reason": "no action.yml readable at " + owner + "/" + repo + "@" + ref,
            "degraded": True}


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------

def fetch_package(http, system, name, version):
    encoded = urlparse.quote(name, safe="")
    url = (DEPSDEV_API + system.upper() + "/packages/" + encoded
           + "/versions/" + urlparse.quote(version, safe=""))
    payload, error = http.get(url)
    fact = {"known": False, "deprecated": False, "reason": None, "repo": None}
    if not error and isinstance(payload, dict):
        block = payload if "isDeprecated" in payload else payload.get("version", payload)
        if isinstance(block, dict):
            fact["known"] = True
            fact["deprecated"] = bool(block.get("isDeprecated"))
            fact["reason"] = block.get("deprecatedReason") or None
            fact["published"] = block.get("publishedAt")
            fact["advisories"] = len(block.get("advisoryKeys") or [])
            fact["repo"] = _repo_from_projects(block)
    elif error == "http 404":
        fact["reason"] = "not found on deps.dev"
    else:
        fact["degraded"] = True
        fact["reason"] = "deps.dev " + str(error)

    if system == "npm" and not fact.get("deprecated"):
        body, npm_error = http.get(NPM_API + urlparse.quote(name, safe="") + "/" + urlparse.quote(version, safe=""))
        if not npm_error and isinstance(body, dict) and body.get("deprecated"):
            fact["known"] = True
            fact["deprecated"] = True
            fact["reason"] = str(body.get("deprecated"))[:300]
    return fact


def _repo_from_projects(block):
    for project in block.get("relatedProjects") or []:
        key = (project or {}).get("projectKey") or {}
        identifier = key.get("id") or ""
        if identifier.startswith("github.com/"):
            return identifier
    for link in block.get("links") or []:
        url = (link or {}).get("url") or ""
        if "github.com/" in url:
            trimmed = url.split("github.com/", 1)[1].rstrip("/")
            trimmed = trimmed[:-4] if trimmed.endswith(".git") else trimmed
            pieces = trimmed.split("/")
            if len(pieces) >= 2:
                return "github.com/" + pieces[0] + "/" + pieces[1]
    return None


def fetch_archived(http, repo_id, token):
    """repo_id looks like github.com/owner/name."""
    pieces = repo_id.split("/")
    if len(pieces) < 3:
        return None
    headers = {"Authorization": "Bearer " + token} if token else {}
    payload, error = http.get(GITHUB_API + pieces[1] + "/" + pieces[2], headers=headers)
    if error or not isinstance(payload, dict):
        return {"known": False, "reason": "github " + str(error), "degraded": error != "http 404"}
    return {
        "known": True,
        "archived": bool(payload.get("archived")),
        "pushed_at": payload.get("pushed_at"),
        "stars": payload.get("stargazers_count"),
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def collect_lookups(surfaces):
    products, actions, packages = set(), {}, {}
    for surface in surfaces:
        for item in surface.get("subjects") or []:
            lookup = item.get("lookup") or {}
            kind = lookup.get("type")
            if kind == "eol" and lookup.get("product"):
                products.add(lookup["product"])
            elif kind == "action":
                key = "%s/%s|%s@%s" % (lookup.get("owner"), lookup.get("repo"),
                                       lookup.get("path") or "", lookup.get("ref"))
                actions[key] = lookup
            elif kind == "package":
                key = "%s:%s:%s" % (lookup.get("system"), lookup.get("name"), lookup.get("version"))
                packages[key] = dict(lookup, direct=bool(item.get("direct")))
    return sorted(products), actions, packages


def run_parallel(items, worker, workers):
    if ThreadPoolExecutor is None or workers <= 1:
        return [worker(item) for item in items]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(worker, items))


VALUE_FLAGS = {"--workers", "--cache-ttl", "--github-budget"}


def main(argv):
    paths = c.positionals(argv, VALUE_FLAGS)
    if not paths:
        c.fail("resolve.py needs one or more surface JSON files")
    surfaces = []
    for path in paths:
        data = c.read_json(path)
        if data is None:
            c.fail("could not read surface file: " + path)
        surfaces.append(data)

    workers = int(c.arg_value(argv, "--workers", "8"))
    ttl = int(c.arg_value(argv, "--cache-ttl", str(DEFAULT_TTL)))
    http = Http(ttl=ttl, use_cache=not c.arg_flag(argv, "--no-cache"))
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    budget = int(c.arg_value(argv, "--github-budget", "200" if token else "15"))

    products, actions, packages = collect_lookups(surfaces)
    facts = {}
    ledger = []

    product_facts = run_parallel(products, lambda p: (p, fetch_product(http, p)), workers)
    degraded_products = 0
    for product, fact in product_facts:
        facts["eol:" + product] = fact
        if fact.get("degraded"):
            degraded_products += 1
    ledger.append(_row("endoflife.date", len(products), degraded_products,
                       "release cycles for " + str(len(products)) + " products"))

    action_items = list(actions.items())
    action_facts = run_parallel(
        action_items,
        lambda pair: (pair[0], fetch_action(http, pair[1]["owner"], pair[1]["repo"],
                                            pair[1].get("path") or "", pair[1]["ref"])),
        workers)
    degraded_actions = 0
    for key, fact in action_facts:
        facts["action:" + key] = fact
        if fact.get("degraded"):
            degraded_actions += 1

    # For every action stuck on a retired runtime, find the nearest major that
    # is not. This is what turns "upgrade it" into "move to @v5".
    stale = [(key, actions[key]) for key, fact in action_facts
             if fact.get("known") and fact.get("using") in DEAD_RUNTIMES]
    upgrades = run_parallel(
        stale,
        lambda pair: (pair[0], find_action_upgrade(http, pair[1]["owner"], pair[1]["repo"],
                                                   pair[1].get("path") or "", pair[1]["ref"])),
        workers)
    for key, upgrade in upgrades:
        if upgrade:
            facts["action:" + key]["upgrade"] = upgrade
    ledger.append(_row("github actions (action.yml)", len(action_items), degraded_actions,
                       "declared runtime for " + str(len(action_items)) + " action refs"))

    package_items = list(packages.items())
    package_facts = run_parallel(
        package_items,
        lambda pair: (pair[0], fetch_package(http, pair[1]["system"], pair[1]["name"], pair[1]["version"])),
        workers)
    degraded_packages = 0
    for key, fact in package_facts:
        facts["pkg:" + key] = fact
        if fact.get("degraded"):
            degraded_packages += 1
    ledger.append(_row("deps.dev + npm", len(package_items), degraded_packages,
                       "deprecation status for " + str(len(package_items)) + " package versions"))

    # Archived upstreams, budget-limited. Deprecated packages first, then direct deps.
    candidates = []
    for key, fact in facts.items():
        if not key.startswith("pkg:") or not isinstance(fact, dict):
            continue
        repo_id = fact.get("repo")
        if not repo_id:
            continue
        lookup = packages.get(key[4:], {})
        candidates.append((0 if fact.get("deprecated") else (1 if lookup.get("direct") else 2), repo_id, key))
    candidates.sort()
    chosen, seen_repos = [], set()
    for _rank, repo_id, key in candidates:
        if repo_id in seen_repos:
            continue
        seen_repos.add(repo_id)
        chosen.append((repo_id, key))
        if len(chosen) >= budget:
            break
    archived_facts = run_parallel(chosen, lambda pair: (pair[0], fetch_archived(http, pair[0], token)),
                                  min(workers, 4))
    archived_by_repo = {}
    degraded_archived = 0
    for repo_id, fact in archived_facts:
        if fact:
            archived_by_repo[repo_id] = fact
            if fact.get("degraded"):
                degraded_archived += 1
    for key, fact in facts.items():
        if key.startswith("pkg:") and isinstance(fact, dict) and fact.get("repo") in archived_by_repo:
            fact["upstream"] = archived_by_repo[fact["repo"]]
    skipped = max(0, len(seen_repos) - len(chosen)) + max(0, len(candidates) - len(chosen))
    note = "archived check for " + str(len(chosen)) + " upstream repositories"
    if skipped and not token:
        note += "; " + str(skipped) + " skipped (60 requests/hour unauthenticated: set GITHUB_TOKEN to raise it)"
    ledger.append(_row("github (archived)", len(chosen), degraded_archived, note))

    c.emit({
        "ok": True,
        "facts": facts,
        "ledger": ledger,
        "http": http.stats,
        "authenticated_github": bool(token),
    })


def _row(source, attempted, degraded, note):
    if attempted == 0:
        status = "skipped"
    elif degraded == 0:
        status = "ok"
    elif degraded < attempted:
        status = "degraded"
    else:
        status = "unavailable"
    return {"source": source, "status": status, "attempted": attempted,
            "degraded": degraded, "note": note}


if __name__ == "__main__":
    main(sys.argv[1:])
