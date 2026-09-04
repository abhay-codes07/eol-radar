"""Offline tests. No network, no clock dependency, stdlib only.

    python -m unittest discover -s tests -v

The scanners are pure filesystem readers, so they are tested directly against
the fixture repository. The verdict engine is tested against a stub fact set,
which keeps the expected output stable no matter what endoflife.date publishes
on the day the suite runs.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
SAMPLE = os.path.join(ROOT, "tests", "fixtures", "sample-repo")
EMPTY = os.path.join(ROOT, "tests", "fixtures", "empty-repo")

sys.path.insert(0, SCRIPTS)

import common as c              # noqa: E402
import eol_data as ed           # noqa: E402
import join                     # noqa: E402
import scan_ci                  # noqa: E402
import scan_cloud               # noqa: E402
import scan_containers          # noqa: E402
import scan_packages            # noqa: E402
import scan_runtimes            # noqa: E402

TODAY = join.parse_date("2026-09-04")


def labels(subjects):
    return [s["label"] for s in subjects]


def by_label(subjects, needle):
    return [s for s in subjects if needle in s["label"]]


class TestMappings(unittest.TestCase):
    def test_split_image(self):
        self.assertEqual(ed.split_image("node:20-alpine")[:2], ("node", "20-alpine"))
        self.assertEqual(ed.split_image("ghcr.io/org/python:3.9")[0], "python")
        self.assertEqual(ed.split_image("ghcr.io/org/python:3.9")[2], "ghcr.io/org")
        self.assertEqual(ed.split_image("node@sha256:abc")[1], "")

    def test_cycle_from_tag(self):
        self.assertEqual(ed.cycle_from_tag("nodejs", "20-alpine")[0], "20")
        self.assertEqual(ed.cycle_from_tag("python", "3.9-slim-bookworm")[0], "3.9")
        self.assertEqual(ed.cycle_from_tag("debian", "bookworm")[0], "12")
        self.assertEqual(ed.cycle_from_tag("ubuntu", "focal")[0], "20.04")
        self.assertIsNone(ed.cycle_from_tag("nodejs", "latest")[0])
        self.assertIsNone(ed.cycle_from_tag("nodejs", "")[0])

    def test_match_cycle_never_guesses(self):
        cycles = ["20", "22", "24", "3.9", "3.10"]
        self.assertEqual(ed.match_cycle("20.11.1", cycles), "20")
        self.assertEqual(ed.match_cycle("3.9.18", cycles), "3.9")
        self.assertEqual(ed.match_cycle("3.10", cycles), "3.10")
        self.assertIsNone(ed.match_cycle("19", cycles))
        self.assertIsNone(ed.match_cycle(None, cycles))

    def test_match_cycle_prefers_the_longer_line(self):
        # 3.1 must not swallow 3.10.
        self.assertEqual(ed.match_cycle("3.10.2", ["3.1", "3.10"]), "3.10")

    def test_action_ref(self):
        self.assertEqual(ed.normalize_action_ref("actions/checkout@v4"),
                         ("actions", "checkout", "", "v4"))
        self.assertEqual(ed.normalize_action_ref("aws/amazon-ecs-deploy/sub@1.2.3"),
                         ("aws", "amazon-ecs-deploy", "sub", "1.2.3"))
        self.assertIsNone(ed.normalize_action_ref("./.github/actions/x"))
        self.assertIsNone(ed.normalize_action_ref("actions/checkout"))

    def test_cdk_constants(self):
        convert = scan_cloud._cdk_constant_to_runtime
        self.assertEqual(convert("NODEJS_20_X"), "nodejs20.x")
        self.assertEqual(convert("PYTHON_3_9"), "python3.9")
        self.assertEqual(convert("JAVA_17"), "java17")
        self.assertEqual(convert("GO_1_X"), "go1.x")
        self.assertEqual(convert("PROVIDED_AL2023"), "provided.al2023")


class TestScanners(unittest.TestCase):
    def test_runtimes(self):
        subjects, _ = scan_runtimes.scan(SAMPLE)
        found = labels(subjects)
        self.assertTrue(any("nodejs 20.11.1" in x for x in found), found)
        self.assertTrue(any("python 3.9.18" in x for x in found), found)
        self.assertTrue(any("engines.node" in x for x in found), found)

    def test_containers_skips_build_stages_and_scratch(self):
        subjects, _ = scan_containers.scan(SAMPLE)
        found = labels(subjects)
        self.assertIn("node:20-alpine", found)          # ARG substituted
        self.assertIn("python:3.9-slim-bookworm", found)
        self.assertIn("postgres:13", found)
        self.assertNotIn("builder", found)              # the build stage, not an image
        self.assertTrue(any("debian 12" in x for x in found), found)

    def test_containers_flags_floating_tags_without_inventing_a_date(self):
        subjects, _ = scan_containers.scan(SAMPLE)
        nginx = by_label(subjects, "nginx:latest")[0]
        self.assertEqual(nginx["lookup"]["type"], "none")

    def test_ci_reads_runners_actions_and_local_actions(self):
        subjects, _ = scan_ci.scan(SAMPLE)
        runners = [s["label"] for s in subjects if s["kind"] == "runner"]
        self.assertIn("ubuntu-20.04", runners)
        self.assertIn("ubuntu-22.04", runners)          # from the matrix
        self.assertIn("macos-14", runners)
        actions = [s for s in subjects if s["kind"] == "action"]
        refs = [s["label"] for s in actions]
        self.assertTrue(any("actions/checkout@v4" == r for r in refs), refs)
        self.assertTrue(any("actions/checkout@v5" == r for r in refs), refs)
        local = [s for s in actions if s.get("resolved_locally")]
        self.assertEqual(len(local), 1, refs)
        self.assertEqual(local[0]["using"], "node20")

    def test_runner_label_shape_rejects_ordinary_hyphenated_words(self):
        find = scan_ci._RUNNER.findall
        for label in ("ubuntu-20.04", "ubuntu-latest", "macos-14", "windows-2022",
                      "ubuntu-24.04-arm", "ubuntu-22.04-arm64", "windows-2025-vs2026"):
            self.assertEqual(find(label), [label], label)
        for text in ("ubuntu-git", "ubuntu-git.Dockerfile", "macos-build", "windows-helper"):
            self.assertEqual(find(text), [], text)

    def test_ci_floating_runner_is_not_a_finding(self):
        subjects, _ = scan_ci.scan(SAMPLE)
        latest = [s for s in subjects if s["label"] == "ubuntu-latest"]
        self.assertEqual(len(latest), 1)
        self.assertTrue(latest[0]["lookup"]["reason"].startswith("floating label"))

    def test_cloud_reads_every_declaration_style(self):
        subjects, _ = scan_cloud.scan(SAMPLE)
        found = labels(subjects)
        self.assertIn("AWS Lambda python3.9", found)     # serverless.yml provider
        self.assertIn("AWS Lambda nodejs20.x", found)    # serverless function + terraform
        self.assertTrue(any("Vercel" in x for x in found), found)

    def test_packages_prefers_manifests_over_lockfiles(self):
        subjects, _, _ = scan_packages.scan(SAMPLE)
        request = [s for s in subjects if s["label"] == "request@2.88.2"]
        self.assertEqual(len(request), 1)
        self.assertTrue(request[0]["where"].startswith("package.json:"), request[0]["where"])
        self.assertTrue(request[0]["direct"])

    def test_packages_maps_frameworks_to_lifecycle_products(self):
        subjects, _, _ = scan_packages.scan(SAMPLE)
        frameworks = [s for s in subjects if s["kind"] == "framework"]
        products = sorted(s["lookup"]["product"] for s in frameworks)
        self.assertIn("django", products)
        self.assertIn("react", products)

    def test_package_cap_is_honoured(self):
        subjects, _, truncated = scan_packages.scan(SAMPLE, max_packages=2)
        packages = [s for s in subjects if s["kind"] == "package"]
        self.assertEqual(len(packages), 2)
        self.assertGreater(truncated, 0)

    def test_empty_repository_is_not_an_error(self):
        for scan in (scan_runtimes.scan, scan_containers.scan, scan_ci.scan, scan_cloud.scan):
            subjects, scanned = scan(EMPTY)
            self.assertEqual(subjects, [])
            self.assertEqual(scanned, 0)
        subjects, scanned, _ = scan_packages.scan(EMPTY)
        self.assertEqual(subjects, [])


STUB_FACTS = {
    "eol:nodejs": {
        "known": True, "newest_maintained": "26", "newest_lts": "24",
        "cycles": {
            "20": {"is_eol": True, "is_maintained": False, "eol": "2026-04-30",
                   "eoas": "2025-10-22", "latest": "20.20.2"},
            "24": {"is_eol": False, "is_maintained": True, "is_lts": True,
                   "eol": "2028-04-30", "eoas": "2026-10-20", "latest": "24.20.0"},
        },
    },
    "eol:python": {
        "known": True, "newest_maintained": "3.14", "newest_lts": None,
        "cycles": {
            "3.9": {"is_eol": True, "is_maintained": False, "eol": "2025-10-31", "latest": "3.9.25"},
            "3.10": {"is_eol": False, "is_maintained": True, "eol": "2026-10-31", "latest": "3.10.21"},
            "3.13": {"is_eol": False, "is_maintained": True, "eol": "2029-10-31", "latest": "3.13.15"},
        },
    },
    "eol:ubuntu": {
        "known": True, "newest_maintained": "26.04", "newest_lts": None,
        "cycles": {"20.04": {"is_eol": True, "is_maintained": True, "eol": "2025-05-31",
                             "eoes": "2030-04-23", "latest": "20.04.6"}},
    },
    "eol:aws-lambda": {
        "known": True, "newest_maintained": "python3.13", "newest_lts": None,
        "cycles": {"python3.9": {"is_eol": False, "is_maintained": False, "eol": "2027-03-03"}},
    },
    "action:actions/checkout|@v4": {"known": True, "using": "node20",
                                    "source": "https://raw.githubusercontent.com/actions/checkout/v4/action.yml"},
    "action:actions/checkout|@v5": {"known": True, "using": "node24",
                                    "source": "https://raw.githubusercontent.com/actions/checkout/v5/action.yml"},
    "action:acme/thing|@v1": {"known": False, "reason": "no action.yml readable", "degraded": True},
    "pkg:npm:request:2.88.2": {"known": True, "deprecated": True,
                               "reason": "request has been deprecated", "repo": "github.com/request/request",
                               "upstream": {"known": True, "archived": False, "pushed_at": "2024-08-14T00:09:41Z"}},
    "pkg:npm:express:4.18.2": {"known": True, "deprecated": False, "repo": "github.com/expressjs/express"},
    "pkg:npm:abandoned:1.0.0": {"known": True, "deprecated": False, "repo": "github.com/x/abandoned",
                                "upstream": {"known": True, "archived": True}},
}

RULES = json.load(open(os.path.join(ROOT, "data", "enforcement.json"), encoding="utf-8"))["rules"]


def verdict(subject, horizon=90):
    return join.evaluate(subject, STUB_FACTS, RULES, TODAY, horizon)


class TestVerdicts(unittest.TestCase):
    def test_past_end_of_life_is_dead(self):
        finding = verdict(c.subject("runtime", "python 3.9", "a:1", "3.9",
                                    c.eol_lookup("python", "3.9.18")))
        self.assertEqual(finding["status"], "DEAD")
        self.assertEqual(finding["date"], "2025-10-31")
        self.assertLess(finding["days"], 0)

    def test_upgrade_advice_prefers_the_current_lts(self):
        finding = verdict(c.subject("runtime", "nodejs 20", "a:1", "20",
                                    c.eol_lookup("nodejs", "20.11.1")))
        self.assertIn("nodejs 24", finding["move_to"])
        self.assertNotIn("26", finding["move_to"])

    def test_expiry_inside_the_horizon_is_dying_and_outside_it_is_not(self):
        # python 3.10 ends 2026-10-31, which is 57 days after the fixed today.
        subject = c.subject("runtime", "python 3.10", "a:1", "3.10", c.eol_lookup("python", "3.10"))
        self.assertEqual(verdict(subject, horizon=90)["status"], "DYING")
        self.assertEqual(verdict(subject, horizon=30)["status"], "WATCH")

    def test_supported_release_is_ok(self):
        finding = verdict(c.subject("runtime", "python 3.13", "a:1", "3.13",
                                    c.eol_lookup("python", "3.13.1")))
        self.assertEqual(finding["status"], "OK")

    def test_action_on_node20_breaks_on_the_enforcement_date(self):
        finding = verdict(c.subject("action", "actions/checkout@v4", "wf:14", "actions/checkout@v4",
                                    c.action_lookup("actions", "checkout", "", "v4")))
        self.assertEqual(finding["status"], "DYING")
        self.assertEqual(finding["date"], "2026-09-23")
        self.assertEqual(finding["date_kind"], "enforcement")
        self.assertIn("node24", finding["move_to"])

    def test_action_on_node24_is_ok(self):
        finding = verdict(c.subject("action", "actions/checkout@v5", "wf:26", "actions/checkout@v5",
                                    c.action_lookup("actions", "checkout", "", "v5")))
        self.assertEqual(finding["status"], "OK")

    def test_local_action_is_judged_like_a_fetched_one(self):
        stale = verdict(c.subject("action", "./x (local)", "wf:1", "./x",
                                  c.no_lookup("local action read from disk"),
                                  extra={"using": "node20", "resolved_locally": True}))
        self.assertEqual(stale["status"], "DYING")
        self.assertEqual(stale["date"], "2026-09-23")
        current = verdict(c.subject("action", "./y (local)", "wf:2", "./y",
                                    c.no_lookup("local action read from disk"),
                                    extra={"using": "node24", "resolved_locally": True}))
        self.assertEqual(current["status"], "OK")

    def test_unreadable_action_is_unknown_not_a_failure(self):
        finding = verdict(c.subject("action", "acme/thing@v1", "wf:3", "acme/thing@v1",
                                    c.action_lookup("acme", "thing", "", "v1")))
        self.assertEqual(finding["status"], "UNKNOWN")
        self.assertIn("unreachable", " ".join(finding["notes"]))

    def test_deprecated_package_is_dead_with_the_publisher_reason(self):
        finding = verdict(c.subject("package", "request@2.88.2", "package.json:9", "request@2.88.2",
                                    c.package_lookup("npm", "request", "2.88.2")))
        self.assertEqual(finding["status"], "DEAD")
        self.assertIn("deprecated by its publisher", finding["because"])

    def test_archived_upstream_is_dead(self):
        finding = verdict(c.subject("package", "abandoned@1.0.0", "p:1", "abandoned@1.0.0",
                                    c.package_lookup("npm", "abandoned", "1.0.0")))
        self.assertEqual(finding["status"], "DEAD")
        self.assertEqual(finding["date_kind"], "archived")

    def test_healthy_package_is_ok(self):
        finding = verdict(c.subject("package", "express@4.18.2", "p:1", "express@4.18.2",
                                    c.package_lookup("npm", "express", "4.18.2")))
        self.assertEqual(finding["status"], "OK")

    def test_extended_support_is_reported_but_does_not_revive_the_release(self):
        finding = verdict(c.subject("image", "ubuntu:20.04", "Dockerfile:1", "ubuntu:20.04",
                                    c.eol_lookup("ubuntu", "20.04")))
        self.assertEqual(finding["status"], "DEAD")
        self.assertIn("extended security maintenance until 2030-04-23", finding["notes"])

    def test_platform_rule_is_reported_even_when_it_does_not_win_the_date(self):
        subject = c.subject("cloud-runtime", "Vercel Node 20", "vercel.json:1", "20",
                            c.eol_lookup("nodejs", "20"), extra={"platform": "vercel", "cycle": "20"})
        finding = verdict(subject)
        self.assertEqual(finding["status"], "DEAD")           # Node 20 already died
        self.assertIn("Vercel disables Node 20 for builds and functions on 2026-10-01",
                      finding["notes"])

    def test_platform_rule_wins_when_it_is_earlier(self):
        subject = c.subject("cloud-runtime", "AWS Lambda python3.9", "serverless.yml:5", "python3.9",
                            c.eol_lookup("aws-lambda", "python3.9"),
                            extra={"platform": "aws-lambda", "cycle": "python3.9"})
        finding = verdict(subject)
        self.assertEqual(finding["date"], "2026-08-31")       # earlier than the 2027 line EOL
        self.assertEqual(finding["status"], "DEAD")

    def test_unknown_product_does_not_invent_a_date(self):
        finding = verdict(c.subject("image", "acme:1", "Dockerfile:1", "acme:1",
                                    c.eol_lookup("not-a-product", "1")))
        self.assertEqual(finding["status"], "UNKNOWN")
        self.assertIsNone(finding["date"])

    def test_floating_runner_label_is_ok(self):
        finding = verdict(c.subject("runner", "ubuntu-latest", "wf:1", "ubuntu-latest",
                                    c.no_lookup("floating label: GitHub moves it to the current image")))
        self.assertEqual(finding["status"], "OK")

    def test_findings_sort_by_what_breaks_first(self):
        subjects = [
            c.subject("runtime", "python 3.13", "a:1", "3.13", c.eol_lookup("python", "3.13")),
            c.subject("action", "actions/checkout@v4", "b:1", "v4",
                      c.action_lookup("actions", "checkout", "", "v4")),
            c.subject("runtime", "python 3.9", "c:1", "3.9", c.eol_lookup("python", "3.9")),
        ]
        findings = sorted((verdict(s) for s in subjects), key=join.sort_key)
        self.assertEqual([f["status"] for f in findings], ["DEAD", "DYING", "OK"])


class TestDiff(unittest.TestCase):
    def test_line_shift_is_not_a_new_finding(self):
        before = {"kind": "action", "what": "actions/checkout@v4", "where": "wf.yml:24", "status": "DYING"}
        after = {"kind": "action", "what": "actions/checkout@v4", "where": "wf.yml:23", "status": "DYING"}
        self.assertEqual(join.finding_key(before), join.finding_key(after))

    def test_diff_reports_new_resolved_and_changed(self):
        baseline = {
            "generated_at": "2026-09-01",
            "findings": [
                {"kind": "action", "what": "gone@v1", "where": "wf.yml:1", "status": "DYING"},
                {"kind": "runtime", "what": "python 3.10", "where": "a:1", "status": "WATCH"},
            ],
        }
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(baseline, handle)
        handle.close()
        try:
            current = [
                {"kind": "runtime", "what": "python 3.10", "where": "a:1", "status": "DYING",
                 "date": None, "days": None},
                {"kind": "image", "what": "postgres:13", "where": "b:1", "status": "DEAD",
                 "date": None, "days": None},
            ]
            diff = join.build_diff(current, handle.name)
            self.assertEqual([f["what"] for f in diff["new"]], ["postgres:13"])
            self.assertEqual([f["what"] for f in diff["resolved"]], ["gone@v1"])
            self.assertEqual(diff["changed"][0]["to"], "DYING")
        finally:
            os.unlink(handle.name)


class TestProcessContract(unittest.TestCase):
    """Every scanner is a process: one JSON object out, exit 0, or a clear fault."""

    def _run(self, script, args):
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + args,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def test_each_scanner_emits_one_json_object(self):
        for script in ("scan_runtimes.py", "scan_containers.py", "scan_ci.py",
                       "scan_cloud.py", "scan_packages.py"):
            result = self._run(script, ["--root", SAMPLE])
            self.assertEqual(result.returncode, 0, script + ": " + result.stderr)
            lines = [line for line in result.stdout.splitlines() if line.strip()]
            self.assertEqual(len(lines), 1, script + " printed " + str(len(lines)) + " lines")
            payload = json.loads(lines[0])
            self.assertTrue(payload["ok"])
            self.assertIn("subjects", payload)

    def test_missing_root_is_a_hard_fault(self):
        result = self._run("scan_ci.py", ["--root", os.path.join(ROOT, "no-such-dir")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a directory", result.stderr)

    def test_absent_surface_degrades_with_a_warning(self):
        result = self._run("scan_cloud.py", ["--root", EMPTY])
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["subjects"], [])
        self.assertIn("no deployment manifests", payload["warning"])

    def test_bad_package_cap_is_rejected(self):
        result = self._run("scan_packages.py", ["--root", SAMPLE, "--max", "0"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("between 1 and 5000", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
