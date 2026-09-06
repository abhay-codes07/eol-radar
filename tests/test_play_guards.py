"""The failures that read as successes, and the guards that stop them.

A step inside a rote Play runs with the run workspace as its working
directory, so a relative root would scan rote's own scratch files and report
on them with a straight face; a scanner that prints its whole result to stdout
can have the preview cut at 65,536 bytes while the step exits 0; a CI gate that
lives inside the report step hides the report when it trips; an empty scan can
look like a clean one. Each of those has a test here.
"""
import contextlib
import io
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

import common as c      # noqa: E402
import gate             # noqa: E402
import join             # noqa: E402
import resolve          # noqa: E402


def run(script, args, cwd=None):
    return subprocess.run([sys.executable, "-B", os.path.join(SCRIPTS, script)] + args,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, cwd=cwd)


class TestRootGuard(unittest.TestCase):
    def test_outside_a_play_a_relative_root_is_fine(self):
        here = os.getcwd()
        os.chdir(SAMPLE)
        try:
            self.assertEqual(c.check_root("."), os.path.abspath(SAMPLE))
        finally:
            os.chdir(here)

    def test_inside_a_play_a_relative_root_is_refused_with_the_fix(self):
        quiet = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(quiet):
            c.check_root(".", in_play=True)
        self.assertIn("root=$PWD", quiet.getvalue())

    def test_inside_a_play_the_rote_workspace_is_refused(self):
        workspace = os.path.join(tempfile.gettempdir(), "home", ".rote", "workspaces", "dag-eol-radar-1234")
        os.makedirs(workspace, exist_ok=True)
        try:
            quiet = io.StringIO()
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(quiet):
                c.check_root(workspace, in_play=True)
            self.assertIn("workspace directory", quiet.getvalue())
        finally:
            os.rmdir(workspace)

    def test_inside_a_play_an_absolute_repository_path_passes(self):
        self.assertEqual(c.check_root(os.path.abspath(SAMPLE), in_play=True), os.path.abspath(SAMPLE))

    def test_a_scanner_refuses_a_relative_root_and_says_what_to_pass(self):
        result = run("scan_ci.py", ["--root", ".", "--in-play"], cwd=SAMPLE)
        self.assertEqual(result.returncode, 1)
        self.assertIn("relative", result.stderr)
        self.assertIn("absolute path", result.stderr)
        self.assertEqual(result.stdout, "")


class TestBoundedStdout(unittest.TestCase):
    def test_with_out_the_file_is_complete_and_stdout_is_a_digest(self):
        workspace = tempfile.mkdtemp()
        try:
            target = os.path.join(workspace, "ci.json")
            result = run("scan_ci.py", ["--root", SAMPLE, "--out", target])
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(target, encoding="utf-8") as handle:
                complete = json.load(handle)
            digest = json.loads(result.stdout)
            self.assertTrue(complete["subjects"], "the fixture repository has CI subjects")
            self.assertNotIn("subjects", digest)
            self.assertEqual(digest["subjects_count"], len(complete["subjects"]))
            self.assertEqual(digest["full_output"], target)
            self.assertEqual(digest["surface"], complete["surface"])
            self.assertLess(len(result.stdout), 2000)
        finally:
            import shutil
            shutil.rmtree(workspace, ignore_errors=True)

    def test_without_out_stdout_is_the_whole_result(self):
        result = run("scan_ci.py", ["--root", SAMPLE])
        payload = json.loads(result.stdout)
        self.assertIn("subjects", payload)
        self.assertNotIn("full_output", payload)


def report_with(counts):
    findings = []
    for status, n in counts.items():
        for i in range(n):
            findings.append({"status": status, "what": "%s-%d" % (status.lower(), i), "where": "f:1",
                             "date": "2026-09-23" if status != "OK" else None, "days": 17,
                             "because": "x", "move_to": None, "kind": "action"})
    full = {status: 0 for status in join.STATUS_ORDER}
    full.update(counts)
    return {"repo": "sample", "generated_at": "2026-09-06", "horizon_days": 90,
            "counts": full, "distinct": dict(full), "findings": findings, "ledger": []}


class TestGateStep(unittest.TestCase):
    def _write(self, report):
        path = os.path.join(tempfile.mkdtemp(), "report.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle)
        return path

    def test_verdict_logic(self):
        self.assertFalse(gate.verdict(report_with({"DEAD": 2}), "none")["tripped"])
        self.assertTrue(gate.verdict(report_with({"DEAD": 2}), "dead")["tripped"])
        self.assertFalse(gate.verdict(report_with({"DYING": 2}), "dead")["tripped"])
        self.assertTrue(gate.verdict(report_with({"DYING": 2}), "dying")["tripped"])
        self.assertFalse(gate.verdict(report_with({"WATCH": 2}), "dying")["tripped"])

    def test_a_tripped_gate_exits_2_with_the_summary_on_stderr_and_a_verdict_on_stdout(self):
        path = self._write(report_with({"DEAD": 1, "OK": 3}))
        result = run("gate.py", ["--report", path, "--fail-on", "dead"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("fail_on=dead tripped", result.stderr)
        self.assertIn("1 dead", result.stderr)
        verdict = json.loads(result.stdout)
        self.assertTrue(verdict["tripped"])

    def test_a_quiet_gate_exits_0(self):
        path = self._write(report_with({"OK": 3}))
        result = run("gate.py", ["--report", path, "--fail-on", "dying"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["tripped"])

    def test_fail_on_is_case_insensitive_and_validated(self):
        path = self._write(report_with({"OK": 3}))
        self.assertEqual(run("gate.py", ["--report", path, "--fail-on", "NONE"]).returncode, 0)
        bad = run("gate.py", ["--report", path, "--fail-on", "sometimes"])
        self.assertEqual(bad.returncode, 1)
        self.assertIn("none, dying or dead", bad.stderr)

    def test_a_missing_report_is_a_hard_fault_not_a_pass(self):
        result = run("gate.py", ["--report", "/nonexistent/report.json", "--fail-on", "none"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not read", result.stderr)


class TestNonObservation(unittest.TestCase):
    def test_an_empty_scan_is_not_reported_as_clean(self):
        report = report_with({})
        summary = join.render_summary(report)
        self.assertIn("non-observation", summary)
        self.assertIn("nothing to check", summary)
        human = join.render_human(dict(report, ownership={"source": None}, ledger=[]))
        self.assertIn("non-observation", human)
        self.assertNotIn("0 dead", summary)

    def test_a_scan_with_subjects_keeps_the_counting_summary(self):
        summary = join.render_summary(report_with({"OK": 4}))
        self.assertIn("0 dead", summary)
        self.assertNotIn("non-observation", summary)


class TestPartialTraversal(unittest.TestCase):
    """os.walk skips a directory it cannot open without a word. The scanners
    record it and join reports the scan as partial."""

    def tearDown(self):
        c.UNREADABLE[:] = []

    def test_an_unreadable_directory_is_recorded_in_the_result(self):
        c.UNREADABLE[:] = []
        handler = c._note_unreadable(os.path.abspath("/repo"))
        handler(OSError(13, "Permission denied", os.path.abspath("/repo/secrets")))
        payload = c.ok("ci", [], None, 0)
        self.assertEqual(payload["unreadable"], ["secrets"])
        self.assertEqual(payload["unreadable_count"], 1)

    def test_join_reports_the_scan_as_partial_in_every_view(self):
        workspace = tempfile.mkdtemp()
        try:
            surface_path = os.path.join(workspace, "ci.json")
            facts_path = os.path.join(workspace, "facts.json")
            surface = c.ok("ci", [], None, 0)
            surface["unreadable"] = ["secrets", "vendor/private"]
            surface["unreadable_count"] = 2
            with open(surface_path, "w", encoding="utf-8") as handle:
                json.dump(surface, handle)
            # The same directory seen by a second scanner is still one directory.
            second_path = os.path.join(workspace, "packages.json")
            second = c.ok("packages", [], None, 0)
            second["unreadable"] = ["secrets"]
            second["unreadable_count"] = 1
            with open(second_path, "w", encoding="utf-8") as handle:
                json.dump(second, handle)
            with open(facts_path, "w", encoding="utf-8") as handle:
                json.dump({"facts": {}, "ledger": []}, handle)
            base = ["join.py", surface_path, second_path, "--facts", facts_path, "--root", SAMPLE,
                    "--today", "2026-09-06"]
            summary = run(base[0], base[1:] + ["--output", "summary"])
            self.assertEqual(summary.returncode, 0, summary.stderr)
            self.assertIn("partial: 2 unreadable directories", summary.stdout)
            human = run(base[0], base[1:] + ["--output", "human"]).stdout
            self.assertIn("PARTIAL: 2 directories could not be read", human)
            self.assertIn("secrets (ci, packages)", human)
            self.assertIn("degraded", human)
            play = json.loads(run(base[0], base[1:] + ["--output", "play"]).stdout)
            self.assertEqual(play["unreadable"], {"ci": ["secrets", "vendor/private"], "packages": ["secrets"]})
        finally:
            import shutil
            shutil.rmtree(workspace, ignore_errors=True)


class TestArchivedCoverageRow(unittest.TestCase):
    """The archived-upstream row reports coverage, not just the lookups it made.

    Chi blú (sookra) ran 0.1.7 on a repository with 300 packages and found the
    row read 'ok' after checking 15 of them, with the shortfall only in the
    note, and only when no token was set."""

    def test_a_budget_shortfall_is_degraded_not_ok(self):
        row = resolve.archived_row(15, 285, 0, 15, authenticated=False)
        self.assertEqual(row["status"], "degraded")
        self.assertEqual((row["checked"], row["skipped"]), (15, 285))
        self.assertIn("285 of 300 not checked", row["note"])
        self.assertIn("GITHUB_TOKEN", row["note"])

    def test_a_token_does_not_make_the_shortfall_disappear(self):
        row = resolve.archived_row(200, 100, 0, 200, authenticated=True)
        self.assertEqual(row["status"], "degraded")
        self.assertIn("100 of 300 not checked", row["note"])
        self.assertIn("--github-budget", row["note"])

    def test_full_coverage_is_ok_and_no_candidates_is_skipped(self):
        self.assertEqual(resolve.archived_row(12, 0, 0, 15, authenticated=False)["status"], "ok")
        self.assertEqual(resolve.archived_row(0, 0, 0, 15, authenticated=False)["status"], "skipped")

    def test_nothing_checked_of_many_is_unavailable(self):
        self.assertEqual(resolve.archived_row(0, 40, 0, 0, authenticated=False)["status"], "unavailable")

    def test_shortfall_counts_repositories_not_package_rows(self):
        candidates = [(0, "a/a", "pkg:npm/x@1"), (1, "a/a", "pkg:npm/y@1"),
                      (1, "b/b", "pkg:npm/z@1"), (2, "c/c", "pkg:npm/w@1")]
        chosen, skipped = resolve.choose_upstreams(candidates, 1)
        self.assertEqual(chosen, [("a/a", "pkg:npm/x@1")])
        self.assertEqual(skipped, 2)
        chosen, skipped = resolve.choose_upstreams(candidates, 10)
        self.assertEqual(len(chosen), 3)
        self.assertEqual(skipped, 0)


class TestRateLimitPhrase(unittest.TestCase):
    def test_reset_time_is_named_when_github_gives_one(self):
        self.assertEqual(resolve._reset_phrase("0"), "; resets 00:00 UTC")
        self.assertEqual(resolve._reset_phrase(None), "")
        self.assertEqual(resolve._reset_phrase("soon"), "")


if __name__ == "__main__":
    unittest.main()
