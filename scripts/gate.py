#!/usr/bin/env python3
"""The CI gate, as its own step.

    python3 gate.py --report work/report.json --fail-on none|dying|dead

Reads the complete report join wrote and exits 2 when a finding of the
requested severity exists. It is a separate step so that a tripped gate never
hides the report: join has already printed its view by the time this runs, so
the presentation shows both the findings and the verdict, and the run still
fails the way a CI job needs it to.

stdout is one small JSON object with the verdict; a tripped gate also writes
the reason to stderr, because a failed step carries its stderr into the run
report.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as c      # noqa: E402
import join             # noqa: E402

VALUES = ("none", "dying", "dead")


def verdict(report, fail_on):
    """The gate decision for a complete report, as a plain dict."""
    counts = report.get("counts") or {}
    dead = int(counts.get("DEAD") or 0)
    dying = int(counts.get("DYING") or 0)
    if fail_on == "dead":
        tripped = dead > 0
    elif fail_on == "dying":
        tripped = dead > 0 or dying > 0
    else:
        tripped = False
    return {
        "fail_on": fail_on,
        "tripped": tripped,
        "dead": dead,
        "dying": dying,
        "summary": join.render_summary(report),
    }


def main(argv):
    fail_on = (c.arg_value(argv, "--fail-on", "none") or "none").strip().lower()
    if fail_on not in VALUES:
        c.fail("fail_on must be none, dying or dead, not " + repr(fail_on))
    path = c.arg_value(argv, "--report")
    if not path:
        c.fail("gate.py needs --report <the report join wrote>")
    report = c.read_json(path)
    if not isinstance(report, dict) or not isinstance(report.get("counts"), dict):
        c.fail("could not read the report at " + str(path))
    result = verdict(report, fail_on)
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    if result["tripped"]:
        # A failed step shows its stderr: say that the gate did its job.
        sys.stderr.write("eol-radar: fail_on=" + fail_on + " tripped. " + result["summary"] + "\n")
        sys.exit(2)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
