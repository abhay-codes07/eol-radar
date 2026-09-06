# Publishing EOL Radar as a rote Play

Published on 6 September 2026 as `abhay-codes07/eol-radar@0.1.7`:
https://play.modiqo.ai/abhay-codes07/eol-radar. This is the procedure that
produced it and the procedure for the next version bump.

Publishing to Community **is** the hackathon entry; there is no separate form.
Everything here runs in **WSL2 Ubuntu**, because rote and play support macOS
and Linux only.

## Run it

```bash
rote play run https://play.modiqo.ai/abhay-codes07/eol-radar root=$PWD --yes
```

`root` is required and must be an absolute path. A step inside a Play runs
with rote's workspace as its working directory, not your shell, so `root=.`
would scan rote's own scratch files and report on them with a straight face.
The scanners refuse a relative path or a path inside `~/.rote/workspaces` and
print the command that works. A bare run is refused by rote itself, because
`root` has no default.

## What a stranger gets

The package a stranger pulls is the whole tool. Nothing is fetched at run time,
nothing is installed, no credential is read, and the only writes are the
run's own `work/` directory inside the workspace:

```
main.ts                                  frontmatter (8 steps) + presentation
deps.toml                                python3 >= 3.8, with brew/apt/dnf install candidates
resources/scripts/*.py                   the twelve modules the steps run and import
resources/data/enforcement.json          the dated platform enforcement calendar
resources/presentation-fixtures/<step>/  one recorded observation per step, for lint
resources/cases/<step>/{partial,truncated,blocked}/
                                         negative cases the presentation must report honestly
```

That is a deliberate change from 0.1.2, which fetched the scanner from GitHub
at a pinned tag with three `git` steps. A reviewer who reads a package before
running it cannot read code that arrives at run time, and a host without `git`
could not run it at all. Now every line the steps execute is in the package,
`python3` is the only host tool, and it is invoked with `-B` so Python writes
no bytecode into the package directory.

Verified on the published package from a directory that had never seen the code:

| check | result |
|---|---|
| `rote play run https://play.modiqo.ai/abhay-codes07/eol-radar root=$PWD --yes` | 8/8 steps, report rendered |
| `sookra/reach-check` | declared 1, reached 1, undeclared none, eligible |
| `sookra/floor-check` | runs at the stock macOS 3.9 floor |
| `sookra/quiet-check` | 0 places where a failure would read as a clean result |
| `play audit run <package> --profile stock-macos --report` | 0 facts, 0 judgments, 0 unknowns |
| `play audit rehearse <package> --profile live,stock-macos,ubuntu-lts` | verdict ready; 24 of 24 negative cases pass |
| `rote play score` | 1.00 |
| `rote play lint` | static pass, runtime pass, no findings |
| files written outside the run workspace | none |

## The Play's shape

Four parameters. `root` is required; the rest have defaults.

| parameter | default | meaning |
|---|---|---|
| `root` | none, required | absolute path of the repository to scan |
| `horizon_days` | `'90'` | expiring inside this window counts as dying |
| `max_packages` | `'300'` | cap on package versions queried |
| `fail_on` | `'none'` | `none`, `dying` or `dead`: the gate step fails the run when matched |

Eight steps in four layers. The five scanners are independent root steps that
read only the filesystem under `root` and write into the run workspace;
`resolve` is the one step that touches the network (public, keyless,
read-only, cached in `work/cache`); `join` turns facts into verdicts; `gate`
is the CI gate.

```
scan_runtimes   ─┐
scan_containers ─┤
scan_ci         ─┼─> resolve ─> join ─> gate
scan_cloud      ─┤
scan_packages   ─┘
```

The gate is its own step on purpose. Inside `join`, a tripped gate would fail
the step and take the report with it; as a step of its own it fails the run
the way a CI job needs, while `join` has already printed the report. The
presentation renders both: the findings, then `GATE TRIPPED: fail_on=dead`.

## What the output refuses to hide

Every one of these came from reading the reviewers in the hackathon channel
run other people's Plays, and each has a test in `tests/test_play_guards.py`:

- **A relative or workspace root** is refused with the command that works
  (see above). This was the bug in the first tweet: `root=.` scanned rote's
  workspace and reported fourteen dead things that belonged to rote.
- **The 64 KiB stdout cap.** rote keeps 65,536 bytes of a step's stdout and
  sets a flag. With `--out`, every scanner and `resolve` print a digest (the
  same fields, the unbounded lists as counts, and the file path) and the next
  step reads the file, so the preview never fills and nothing downstream
  depends on it. `join` emits a bounded view within a byte budget and declares
  what it left out. The presentation still reads `stdout.truncated` and the
  byte count on every step and says so in a NOTES block if a preview was cut.
- **A scanner that failed, was blocked or was skipped** is listed under
  INCOMPLETE, the summary line gains `incomplete: <steps>`, and the JSON
  result carries `degraded`. A timed-out step reads as a timeout, because the
  presentation reads `output.diagnostic` as well as `output.message`.
- **A directory the walk could not open** is recorded by every scanner
  (`os.walk` skips it in silence by default), the ledger row goes `degraded`,
  and the report says `PARTIAL: 1 directory could not be read: secrets`.
  This one was found by `sookra/quiet-check` on 0.1.4.
- **An empty scan** prints `nothing to check ... non-observation, not a clean
  bill of health`, never `0 dead`.
- **A GitHub rate-limit 403** is reported as the caller's quota with the
  reset time, not as a fact about the repository.

## The package is built from this repository

`play/main.ts` and `play/deps.toml` are the Play exactly as published. The
package is assembled by one script, and that script is the only way it is built:

```bash
python3 play/pack.py ~/.rote/flows/local-process     # the directory `rote play info eol-radar` reports
```

It copies the two files to the package root, the scanner modules to
`resources/scripts/`, the enforcement calendar to `resources/data/`, and the
recorded fixtures and negative cases from `play/resources/`. Then it checks that
every `@resource{...}` token and every declared fixture in the frontmatter names
a file that now exists, and parses every packaged script as Python 3.8 so the
declared floor stays true. `tests/test_play_package.py` runs the same checks and
more: eight steps in four layers, every step runs packaged code with the system
`python3 -B`, nothing in argv fetches anything or reaches outside the
workspace, only `resolve` imports a network module, `root` is required.

## Version bump: the whole cycle

Every published version is immutable, so every fix is a bump. The order below
matters, for two reasons that cost an hour to learn:

- `rote play release` refuses to run without `.rote-hardcode-audit.json` next to
  `main.ts`, and consumes it. Only `rote workspace export` writes it, and only
  when the `main.ts` it merges into is a **draft**. So: pack first (the
  repository copy is a draft), export second, then pack again.
- The release gate scans every file under `resources/` for the literal parameter
  values that were recorded during capture. `300` and `none` occur in the
  scanner's own source, so a capture with `max_packages=300 fail_on=none` can
  never be released once the scanner ships inside the package. Capture with
  values that cannot occur in the code (`257`, `NONE`; `gate` lowercases
  `fail_on`, so `NONE` is valid).

```bash
# 0. bump metadata.version in play/main.ts; run the tests
python3 -m unittest discover -s tests

# 1. pack, run once, record the fixtures and negative cases, carry them back
F=~/.rote/flows/local-process
T=/home/abhay/eol-targets/starter-workflows
python3 play/pack.py "$F"
rote play run eol-radar root=$T
play audit fixtures "$F"                     # writes resources/presentation-fixtures, resources/cases; declares them in main.ts
cp -r "$F/resources/presentation-fixtures" "$F/resources/cases" play/resources/ && cp "$F/main.ts" play/main.ts
play audit rehearse "$F" --profile live,stock-macos,ubuntu-lts      # verdict: ready

# 2. a clean workspace holding the shipped scanner, one reading per step
rote init eol-radar-v8 --seq --force && cd ~/.rote/workspaces/eol-radar-v8
cp -r "$F/resources" ./resources && rm -rf resources/presentation-fixtures resources/cases && mkdir -p work
rote workspace set root=$T horizon_days=90 max_packages=257 fail_on=NONE
for s in runtimes containers ci cloud; do
  rote proc run python3 -B resources/scripts/scan_$s.py --root $T --out work/$s.json --in-play
done
rote proc run python3 -B resources/scripts/scan_packages.py --root $T --max 257 --out work/packages.json --in-play
rote proc run python3 -B resources/scripts/resolve.py work/runtimes.json work/containers.json work/ci.json work/cloud.json work/packages.json --cache work/cache --out work/facts.json
rote proc run python3 -B resources/scripts/join.py work/runtimes.json work/containers.json work/ci.json work/cloud.json work/packages.json --facts work/facts.json --root $T --horizon 90 --output play --out work/report.json
rote proc run python3 -B resources/scripts/gate.py --report work/report.json --fail-on NONE
rote play pending write eol-radar --name eol-radar --description "..." --notes "..."

# 3. export writes the hardcode audit; carry its block into the repository copy
rote workspace export "$F/main.ts" --params root,horizon_days,max_packages,fail_on -d "..."
#    copy the three `hardcode_audit:` lines from $F/main.ts into play/main.ts

# 4. pack again (restores the real main.ts, keeps the audit), lint, release
python3 play/pack.py "$F"
rote play lint eol-radar
rote play release eol-radar                    # status: draft -> released, readiness: ready

# 5. publish, then verify from a directory that has never seen the code
rote registry play push "$F" abhay-codes07 --dry-run
rote registry play push "$F" abhay-codes07
cd /some/repo && rote play run https://play.modiqo.ai/abhay-codes07/eol-radar root=$PWD --yes
rote play pending discard eol-radar
```

Rules that bite: quote non-string defaults (`'90'`, not `90`); no literal `*/`
in the frontmatter; every step gets a `timeout_ms`; never `console.log` in a
step; a local play takes named parameters and no `--yes`, a registry play needs
`--yes`; `deps.toml` needs `schema_version = 1`; the export cannot reify a value
that also occurs elsewhere, so `horizon_days` is wired in by hand and stays so;
`rote play score` wants a top-level `tags:` list and an `output:` block with a
schema, or it stops at 0.62.

## Test the negative space

```bash
rote play run eol-radar root=$T                             # happy path, twice: it must be re-runnable
rote play run eol-radar root=/tmp/empty-dir                 # non-observation, exit 0
rote play run eol-radar root=.                              # refused: relative root, with the fix
rote play run eol-radar                                     # refused by rote: root is required
rote play run eol-radar root=~/.rote/workspaces/dag-...     # refused: inside the workspace
rote play run eol-radar root=/nonexistent                   # five scanners fail with the reason; the rest blocked
rote play run eol-radar root=$T horizon_days=abc            # join rejects the input, says why
rote play run eol-radar root=$T fail_on=dead                # report rendered, then GATE TRIPPED, run fails
rote play run eol-radar root=$T fail_on=maybe               # gate fails with the valid values
rote play run eol-radar root=/tmp/partial-repo              # a chmod 000 directory: PARTIAL, named
rote play run eol-radar root=$T --output=summary            # one line, same facts
rote play run eol-radar root=$T --output=json               # the whole report under .report
```

All of these were run against 0.1.7 before it was pushed. The offline suite
covers the same ground without rote: `python3 -m unittest discover -s tests`.

## Schedule it

Explicitly rewarded, and this Play is built for it: the countdown to
23 September moves every day.

```bash
play recurring probe
play recurring schedule --reference abhay-codes07/eol-radar@0.1.7 --cadence daily --for 6d \
  --parameter root=/path/to/your/repo --cwd /path/to/your/repo \
  --why "Daily death calendar for my own repo: counts down to the Node 20 runner removal on 2026-09-23"
play recurring list
```

## The wrong turns to keep

The first version of the CI scanner treated `actions/checkout@v4` as current
because v4 is the latest major. It is not current in the way that matters: its
`action.yml` declares `runs.using: node20`, and GitHub removes Node 20 from the
runners on 23 September. The fix was to read `action.yml` at the exact ref
instead of trusting the tag. That correction is the insight the whole tool
rests on.

The second was shipping a Play that cloned its own code at run time. It
worked, and it was the wrong shape for a tool that asks to be trusted with a
repository.

The third was `root=.` in the launch tweet. It ran, it printed fourteen dead
things, and every one of them belonged to rote's workspace. A confident answer
about the wrong directory is worse than no answer, which is why 0.1.4 refuses
it and says what to pass.
