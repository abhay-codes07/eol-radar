"""Local runner: the same steps rote executes, in the same order.

    scan_runtimes ─┐
    scan_containers┤
    scan_ci        ├─> resolve ─> join ─> human | summary | json
    scan_cloud     ┤
    scan_packages ─┘

The five scanners are independent roots and never touch the network, so they
parallelise cleanly and stay reproducible. Everything below exists so the Play
can be developed and tested without rote installed; the published Play runs the
identical scripts.
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as c

HERE = os.path.dirname(os.path.abspath(__file__))

SURFACES = [
    ("runtimes", "scan_runtimes.py", []),
    ("containers", "scan_containers.py", []),
    ("ci", "scan_ci.py", []),
    ("cloud", "scan_cloud.py", []),
    ("packages", "scan_packages.py", ["--max"]),
]

USAGE = """eol-radar - everything in a repository with a death date, soonest first.

usage: python scripts/eol_radar.py [options]

  --root PATH          repository to scan (default: .)
  --repo OWNER/NAME    public GitHub repository to shallow-clone and scan
  --user NAME          scan every repository of a GitHub user or org and roll
                       the results into one account-wide view
  --max-repos N        how many repositories --user scans (10)
  --include-forks      include forks, which --user skips by default
  --horizon DAYS       anything expiring within this window counts as dying (90)
  --no-packages        skip the dependency surface (fastest)
  --max-packages N     cap on package versions queried (300)
  --baseline FILE      previous --output json result, to show what changed
  --fail-on WHAT       none | dying | dead   exit 2 when matched (none)
  --output VIEW        human | summary | json | patch (human)
                       patch prints a unified diff of the mechanical fixes,
                       ready for `git apply`. It writes nothing itself.
  --depth N            directory depth to search (8)
  --today YYYY-MM-DD   evaluate against a fixed date (for tests)
  --keep-work          keep the intermediate step files and print their path
  --no-cache           ignore the on-disk HTTP cache
"""


def run_step(argv, capture_to=None):
    """Run one step the way rote does: a process, its stdout, its exit code."""
    result = subprocess.run([sys.executable] + argv, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, universal_newlines=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr or "")
        c.fail("step failed: " + " ".join(os.path.basename(a) for a in argv[:1]), result.returncode)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if capture_to:
        with open(capture_to, "w", encoding="utf-8") as handle:
            handle.write(result.stdout)
    return result.stdout


def list_repositories(owner, include_forks, limit):
    """Public repositories for a user or an organisation, newest push first.

    Anonymous requests are enough for public repositories. GITHUB_TOKEN, if the
    environment already has one, lifts the rate limit and reveals private repos
    the caller can see. Nothing is written and no credential is ever required.
    """
    from urllib import request as urlrequest, error as urlerror

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    collected = []
    for shape in ("users", "orgs"):
        page = 1
        while len(collected) < limit and page <= 5:
            url = ("https://api.github.com/%s/%s/repos?per_page=100&sort=pushed&page=%d"
                   % (shape, owner, page))
            request = urlrequest.Request(url)
            request.add_header("User-Agent", "eol-radar")
            request.add_header("Accept", "application/vnd.github+json")
            if token:
                request.add_header("Authorization", "Bearer " + token)
            try:
                with urlrequest.urlopen(request, timeout=30) as response:
                    import json as _json
                    batch = _json.loads(response.read().decode("utf-8", "replace"))
            except urlerror.HTTPError as exc:
                if exc.code == 404 and shape == "users":
                    break                      # not a user; try the org endpoint
                if exc.code in (403, 429):
                    c.fail("GitHub rate limit reached while listing repositories. "
                           "Set GITHUB_TOKEN to raise it from 60 requests an hour.")
                c.fail("could not list repositories for " + owner + ": http " + str(exc.code))
            except Exception as exc:           # network trouble is a hard fault here
                c.fail("could not reach GitHub: " + str(exc)[:160])
            if not isinstance(batch, list) or not batch:
                break
            for item in batch:
                if not isinstance(item, dict):
                    continue
                if item.get("archived"):
                    continue
                if item.get("fork") and not include_forks:
                    continue
                collected.append(item["full_name"])
            page += 1
        if collected:
            break
    if not collected:
        c.fail("no repositories found for " + owner
               + " (private ones need GITHUB_TOKEN, forks need --include-forks)")
    return collected[:limit]


def scan_account(owner, scratch, argv, output, today, horizon):
    """Scan every repository of an account and roll the results into one view."""
    include_forks = c.arg_flag(argv, "--include-forks")
    limit = int(c.arg_value(argv, "--max-repos", "10"))
    if limit < 1 or limit > 100:
        c.fail("--max-repos must be between 1 and 100")
    names = list_repositories(owner, include_forks, limit)
    sys.stderr.write("eol-radar: scanning %d repositories for %s\n" % (len(names), owner))

    reports = []
    for index, full_name in enumerate(names, start=1):
        sys.stderr.write("  [%d/%d] %s\n" % (index, len(names), full_name))
        target = os.path.join(scratch, "report-%02d.json" % index)
        step = [os.path.abspath(__file__), "--repo", full_name, "--output", "json",
                "--horizon", horizon,
                "--max-packages", c.arg_value(argv, "--max-packages", "150")]
        if today:
            step += ["--today", today]
        if c.arg_flag(argv, "--no-packages"):
            step.append("--no-packages")
        result = subprocess.run([sys.executable] + step, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)
        if result.returncode != 0 or not result.stdout.strip():
            sys.stderr.write("      skipped: " + (result.stderr or "no output").strip()[:120] + "\n")
            continue
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(result.stdout)
        reports.append(target)

    if not reports:
        c.fail("no repository could be scanned")
    step = [os.path.join(HERE, "aggregate.py")] + reports + [
        "--owner", owner, "--output", output, "--horizon", horizon]
    if today:
        step += ["--today", today]
    result = subprocess.run([sys.executable] + step, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, universal_newlines=True)
    sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


def clone(repo, into):
    if "/" not in repo or repo.count("/") != 1 or repo.startswith("-"):
        c.fail("--repo must look like owner/name")
    if not shutil.which("git"):
        c.fail("git is required for --repo but was not found on PATH")
    url = "https://github.com/" + repo + ".git"
    target = os.path.join(into, repo.split("/")[1])
    result = subprocess.run(["git", "clone", "--depth", "1", "--quiet", url, target],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if result.returncode != 0:
        c.fail("could not clone " + repo + ": " + (result.stderr or "").strip()[:200])
    return target


def main(argv):
    if "--help" in argv or "-h" in argv:
        sys.stdout.write(USAGE)
        return

    root = c.arg_value(argv, "--root", ".")
    repo = c.arg_value(argv, "--repo")
    horizon = c.arg_value(argv, "--horizon", "90")
    max_packages = c.arg_value(argv, "--max-packages", "300")
    depth = c.arg_value(argv, "--depth", "8")
    baseline = c.arg_value(argv, "--baseline")
    fail_on = c.arg_value(argv, "--fail-on", "none")
    output = c.arg_value(argv, "--output", "human")
    today = c.arg_value(argv, "--today")
    include_packages = not c.arg_flag(argv, "--no-packages")
    keep_work = c.arg_flag(argv, "--keep-work")

    account = c.arg_value(argv, "--user") or c.arg_value(argv, "--org")
    scratch = tempfile.mkdtemp(prefix="eol-radar-")
    try:
        if account:
            if "/" in account or account.startswith("-"):
                c.fail("--user takes an account name, not owner/name")
            sys.exit(scan_account(account, scratch, argv, output, today, horizon))
        if repo:
            root = clone(repo, scratch)
            repo_name = repo
        else:
            root = c.check_root(root)
            repo_name = os.path.basename(os.path.abspath(root)) or "(root)"

        surface_files = []
        for name, script, extra in SURFACES:
            if name == "packages" and not include_packages:
                continue
            step = [os.path.join(HERE, script), "--root", root, "--depth", depth]
            if "--max" in extra:
                step += ["--max", max_packages]
            path = os.path.join(scratch, name + ".json")
            run_step(step, capture_to=path)
            surface_files.append(path)

        facts_path = os.path.join(scratch, "facts.json")
        resolve_step = [os.path.join(HERE, "resolve.py")] + surface_files
        if c.arg_flag(argv, "--no-cache"):
            resolve_step.append("--no-cache")
        run_step(resolve_step, capture_to=facts_path)

        join_step = ([os.path.join(HERE, "join.py")] + surface_files
                     + ["--facts", facts_path, "--horizon", horizon,
                        "--repo-name", repo_name, "--output", output,
                        "--fail-on", fail_on, "--root", root])
        if baseline:
            join_step += ["--baseline", baseline]
        if today:
            join_step += ["--today", today]

        # The patch view has to survive this hop byte for byte: newline
        # translation here would undo the care taken in join.py and produce a
        # diff git refuses to apply.
        text_mode = output != "patch"
        result = subprocess.run([sys.executable] + join_step, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=text_mode)
        if text_mode:
            sys.stdout.write(result.stdout)
        else:
            stream = getattr(sys.stdout, "buffer", None)
            if stream is None:
                sys.stdout.write(result.stdout.decode("utf-8", "replace"))
            else:
                stream.write(result.stdout)
                stream.flush()
        if result.stderr:
            err = result.stderr
            sys.stderr.write(err if isinstance(err, str) else err.decode("utf-8", "replace"))
        if keep_work:
            sys.stderr.write("step files kept in " + scratch + "\n")
        sys.exit(result.returncode)
    finally:
        if not keep_work:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1:])
