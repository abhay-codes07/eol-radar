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
  --horizon DAYS       anything expiring within this window counts as dying (90)
  --no-packages        skip the dependency surface (fastest)
  --max-packages N     cap on package versions queried (300)
  --baseline FILE      previous --output json result, to show what changed
  --fail-on WHAT       none | dying | dead   exit 2 when matched (none)
  --output VIEW        human | summary | json (human)
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

    scratch = tempfile.mkdtemp(prefix="eol-radar-")
    try:
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
                        "--repo-name", repo_name, "--output", output, "--fail-on", fail_on])
        if baseline:
            join_step += ["--baseline", baseline]
        if today:
            join_step += ["--today", today]

        result = subprocess.run([sys.executable] + join_step, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)
        sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        if keep_work:
            sys.stderr.write("step files kept in " + scratch + "\n")
        sys.exit(result.returncode)
    finally:
        if not keep_work:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1:])
