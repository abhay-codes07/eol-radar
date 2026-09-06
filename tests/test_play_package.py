"""The rote Play package: what a stranger pulls must be complete, readable
before it runs, and honest about the host it needs.

These tests parse play/main.ts the way a reviewer reads it: the frontmatter
steps are the whole effect plane, so every command they run must point at a
file that ships inside the package, nothing may be fetched at run time, and the
Python floor deps.toml declares must be the floor the packaged code has.
"""
import ast
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAY = os.path.join(REPO, "play")
sys.path.insert(0, PLAY)

import pack  # noqa: E402

MAIN_TS = os.path.join(PLAY, "main.ts")
DEPS = os.path.join(PLAY, "deps.toml")
SCANNERS = ["scan_runtimes", "scan_containers", "scan_ci", "scan_cloud", "scan_packages"]


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def steps_of(frontmatter):
    """The steps: block as {name: {"argv": [...], "depends_on": [...], "timeout_ms": int}}."""
    lines = frontmatter.splitlines()
    start = lines.index("steps:")
    steps = {}
    current = None
    for line in lines[start + 1:]:
        if line and not line.startswith(" "):
            break
        m = re.match(r"^  (\w+):$", line)
        if m:
            current = {"argv": [], "depends_on": [], "timeout_ms": None}
            steps[m.group(1)] = current
            continue
        m = re.match(r"^    depends_on: \[(.*)\]$", line)
        if m:
            current["depends_on"] = [x.strip() for x in m.group(1).split(",") if x.strip()]
            continue
        m = re.match(r"^    timeout_ms: (\d+)$", line)
        if m:
            current["timeout_ms"] = int(m.group(1))
            continue
        m = re.match(r"^    - (.*)$", line)
        if m:
            current["argv"].append(m.group(1).strip("'\""))
    return steps


def field(frontmatter, key):
    m = re.search(r"^\s*%s: (.+)$" % re.escape(key), frontmatter, re.M)
    return m.group(1).strip().strip("'\"") if m else None


class TestPackageContents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eol-radar-pack-")
        self.written, self.problems = pack.pack(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pack_reports_no_problems(self):
        self.assertEqual(self.problems, [])

    def test_every_resource_the_steps_name_is_in_the_package(self):
        tokens = pack.resource_tokens(os.path.join(self.tmp, "main.ts"))
        self.assertTrue(tokens, "the steps reference no packaged resource at all")
        for token in tokens:
            self.assertTrue(os.path.isfile(os.path.join(self.tmp, "resources", token)), token)

    def test_the_whole_import_closure_ships(self):
        # Every module a packaged script imports from its own directory must
        # be packaged too, or the step fails on a machine that has no clone.
        scripts = os.path.join(self.tmp, "resources", "scripts")
        shipped = {name[:-3] for name in os.listdir(scripts) if name.endswith(".py")}
        for name in sorted(shipped):
            tree = ast.parse(read_text(os.path.join(scripts, name + ".py")))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        if top in {"common", "eol_data", "migrate", "ownership", "join", "resolve"} or \
                                top.startswith("scan_"):
                            self.assertIn(top, shipped, "%s imports %s, which is not packaged" % (name, top))

    def test_declared_presentation_fixtures_exist(self):
        paths = pack.fixture_paths(os.path.join(self.tmp, "main.ts"))
        self.assertEqual(len(paths), 7, paths)
        for path in paths:
            self.assertTrue(os.path.isfile(os.path.join(self.tmp, path)), path)

    def test_negative_cases_cover_every_step(self):
        cases = os.path.join(self.tmp, "resources", "cases")
        fm = pack.frontmatter(os.path.join(self.tmp, "main.ts"))
        for name in steps_of(fm):
            for case in ("partial", "truncated", "blocked"):
                self.assertTrue(os.path.isfile(os.path.join(cases, name, case, "observation.json")),
                                "%s has no %s case" % (name, case))

    def test_package_has_no_python_bytecode_or_clutter(self):
        for base, dirs, files in os.walk(self.tmp):
            self.assertNotIn("__pycache__", dirs, base)
            for f in files:
                self.assertFalse(f.endswith((".pyc", ".pyo")), f)


class TestStepsAreSelfContained(unittest.TestCase):
    def setUp(self):
        self.fm = pack.frontmatter(MAIN_TS)
        self.steps = steps_of(self.fm)

    def test_seven_steps_in_three_layers(self):
        self.assertEqual(sorted(self.steps), sorted(SCANNERS + ["resolve", "join"]))
        for name in SCANNERS:
            self.assertEqual(self.steps[name]["depends_on"], [], name + " should be a root step")
        self.assertEqual(sorted(self.steps["resolve"]["depends_on"]), sorted(SCANNERS))
        self.assertEqual(self.steps["join"]["depends_on"], ["resolve"])

    def test_every_step_runs_a_packaged_script_with_the_system_python(self):
        for name, step in self.steps.items():
            argv = step["argv"]
            self.assertEqual(argv[:2], ["python3", "-B"], name)
            self.assertRegex(argv[2], r"^@resource\{scripts/[a-z_]+\.py\}$", name)

    def test_nothing_is_fetched_or_cloned_at_run_time(self):
        for name, step in self.steps.items():
            for arg in step["argv"]:
                for forbidden in ("http://", "https://", "github.com", "eol-radar-tool"):
                    self.assertNotIn(forbidden, arg, "%s argv fetches something at run time" % name)
            self.assertNotEqual(step["argv"][0], "git", name)

    def test_every_step_has_a_timeout(self):
        for name, step in self.steps.items():
            self.assertIsNotNone(step["timeout_ms"], name)
            self.assertGreaterEqual(step["timeout_ms"], 30000, name)

    def test_only_the_resolve_step_reaches_the_network(self):
        # The scanners and join must not import anything that opens a socket;
        # resolve is the one step allowed to, and it only reads.
        network = {"urllib", "http", "socket", "ssl", "ftplib", "smtplib", "requests"}
        for name in SCANNERS + ["join"]:
            path = os.path.join(REPO, "scripts", name + ".py")
            tree = ast.parse(read_text(path))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module.split(".")[0]]
                for mod in mods:
                    self.assertNotIn(mod, network, "%s imports %s" % (name, mod))

    def test_parameters_are_the_four_documented_ones(self):
        names = re.findall(r"^\s*- name: (\w+)$", self.fm, re.M)
        self.assertEqual(names, ["root", "horizon_days", "max_packages", "fail_on"])
        for line in re.findall(r"^\s*default: (.+)$", self.fm, re.M):
            self.assertRegex(line, r"^'.*'$", "non-string defaults must be quoted: " + line)

    def test_status_is_draft_in_source_so_release_is_explicit(self):
        self.assertEqual(field(self.fm, "status"), "draft")
        self.assertEqual(field(self.fm, "execution_model"), "steps_with_presentation")


class TestDeclaredHost(unittest.TestCase):
    def test_deps_declare_python3_and_nothing_else(self):
        text = read_text(DEPS)
        self.assertEqual(text.count("[[tools]]"), 1)
        self.assertIn('id = "python3"', text)
        self.assertIn('version_requirement = ">=3.8"', text)
        self.assertNotIn("git", text.split("\n\n", 1)[-1].replace("nothing but", ""))

    def test_packaged_scripts_parse_as_python_38(self):
        for name in pack.PACKAGE_SCRIPTS:
            path = os.path.join(REPO, "scripts", name)
            ast.parse(read_text(path), path, feature_version=pack.FLOOR)

    def test_no_stdlib_newer_than_the_floor(self):
        # Modules and methods that arrived after 3.8 must not appear in the
        # packaged code, or the declared floor is a lie.
        newer = [r"\.removeprefix\(", r"\.removesuffix\(", r"\bfunctools\.cache\b", r"\bzoneinfo\b",
                 r"\bgraphlib\b", r"\bast\.unparse\b", r"\bmath\.lcm\b", r"\btomllib\b"]
        for name in pack.PACKAGE_SCRIPTS:
            src = read_text(os.path.join(REPO, "scripts", name))
            for pattern in newer:
                self.assertIsNone(re.search(pattern, src), "%s uses %s" % (name, pattern))


class TestShippedEvidence(unittest.TestCase):
    def test_join_fixture_is_a_real_bounded_report(self):
        path = os.path.join(PLAY, "resources", "presentation-fixtures", "join", "stdout.json")
        report = json.loads(read_text(path))
        self.assertEqual(report["view"], "play")
        for key in ("summary", "counts", "findings", "ledger"):
            self.assertIn(key, report)
        self.assertLessEqual(os.path.getsize(path), 48000)

    def test_fixture_evidence_names_no_author_paths(self):
        # Fixtures travel in the package; nothing in them may point at the
        # author's machine.
        root = os.path.join(PLAY, "resources", "presentation-fixtures")
        for base, _dirs, files in os.walk(root):
            for f in files:
                text = read_text(os.path.join(base, f))
                self.assertNotIn("/home/abhay", text, os.path.join(base, f))
                self.assertNotIn("C:\\Users", text, os.path.join(base, f))


if __name__ == "__main__":
    unittest.main()
