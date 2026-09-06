# Publishing EOL Radar as a rote Play

Published on 6 September 2026 as `abhay-codes07/eol-radar@0.1.3`:
https://play.modiqo.ai/abhay-codes07/eol-radar. This is the procedure that
produced it and the procedure for the next version bump.

Publishing to Community **is** the hackathon entry; there is no separate form.
Everything here runs in **WSL2 Ubuntu**, because rote and play support macOS
and Linux only.

## What a stranger gets

The package a stranger pulls is the whole tool. Nothing is fetched at run time,
nothing is installed, and no credential is read:

```
main.ts                                  frontmatter (7 steps) + presentation
deps.toml                                python3 >= 3.8, with brew/apt/dnf install candidates
resources/scripts/*.py                   the eleven modules the steps run and import
resources/data/enforcement.json          the dated platform enforcement calendar
resources/presentation-fixtures/<step>/  one recorded observation per step, for lint
resources/cases/<step>/{partial,truncated,blocked}/
                                         negative cases the presentation must report honestly
```

That is a deliberate change from 0.1.2, which fetched the scanner from GitHub
at a pinned tag with three `git` steps. A reviewer who reads a package before
running it cannot read code that arrives at run time, and a host without `git`
could not run it at all. Now every line the steps execute is in the package,
`python3` is the only host tool, and it is invoked with `-B` so Python writes no
bytecode into the package directory.

Verified on the published package from a directory that had never seen the code:

| check | result |
|---|---|
| `rote play run https://play.modiqo.ai/abhay-codes07/eol-radar root=<repo> --yes` | 7/7 steps, report rendered |
| `sookra/reach-check` | declared 1, reached 1, undeclared none, eligible |
| `play audit run <package> --profile stock-macos --report` | 0 facts, 0 judgments, 0 unknowns |
| `play audit rehearse <package> --profile live,stock-macos,ubuntu-lts` | verdict ready; 21 of 21 negative cases pass |
| `rote play lint` | static pass, runtime pass, no findings |

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
a few more (seven steps in three layers, every step runs packaged code, nothing
in argv fetches anything, only `resolve` imports a network module).

## The Play's shape

Four parameters, all optional, all plain strings.

| parameter | default | meaning |
|---|---|---|
| `root` | `.` | repository to scan |
| `horizon_days` | `'90'` | expiring inside this window counts as dying |
| `max_packages` | `'300'` | cap on package versions queried |
| `fail_on` | `'none'` | `none`, `dying` or `dead`: the run fails when matched |

Seven steps in three layers. The five scanners are independent root steps that
read only the filesystem under `root` and write into the run workspace;
`resolve` is the one step that touches the network (public, keyless, read-only);
`join` turns facts into verdicts.

```
scan_runtimes   ─┐
scan_containers ─┤
scan_ci         ─┼─> resolve ─> join
scan_cloud      ─┤
scan_packages   ─┘
```

`join` emits its bounded `play` view, because rote previews only 65,536 bytes of
a step's stdout; the complete report is written to `work/report.json` in the run
workspace, and the view says how many low-severity findings it left out.

The presentation reads every step, not only `join`. A scanner that failed, was
blocked, or had its stdout preview truncated is named in an `INCOMPLETE` block
at the top of the human view, the summary line gains `incomplete: <steps>`, and
the JSON result carries `degraded`. That is what `play audit rehearse` checks
with the packed negative cases, and it is why they all pass.

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
  values that cannot occur in the code (`257`, `NONE`; `join` lowercases
  `fail_on`, so `NONE` is valid).

```bash
# 0. bump metadata.version in play/main.ts; run the tests
python3 -m unittest discover -s tests

# 1. a clean workspace holding the shipped scanner, one reading per step
F=~/.rote/flows/local-process
TARGET=/home/abhay/eol-targets/starter-workflows
rote init eol-radar-v3 --seq --force
cd ~/.rote/workspaces/eol-radar-v3
python3 /path/to/eol-radar/play/pack.py "$F"
cp -r "$F/resources" ./resources && rm -rf resources/presentation-fixtures resources/cases && mkdir -p work
rote workspace set root=$TARGET horizon_days=90 max_packages=257 fail_on=NONE
for s in runtimes containers ci cloud; do
  rote proc run python3 -B resources/scripts/scan_$s.py --root $TARGET --out work/$s.json
done
rote proc run python3 -B resources/scripts/scan_packages.py --root $TARGET --max 257 --out work/packages.json
rote proc run python3 -B resources/scripts/resolve.py work/runtimes.json work/containers.json work/ci.json work/cloud.json work/packages.json --out work/facts.json
rote proc run python3 -B resources/scripts/join.py work/runtimes.json work/containers.json work/ci.json work/cloud.json work/packages.json --facts work/facts.json --root $TARGET --horizon 90 --fail-on NONE --output play --out work/report.json
rote play pending write eol-radar --name eol-radar --description "..." --notes "..."

# 2. export writes the hardcode audit; carry its block into the repository copy
rote workspace export "$F/main.ts" --params root,horizon_days,max_packages,fail_on -d "..."
#    copy the three `hardcode_audit:` lines from $F/main.ts into play/main.ts

# 3. pack again (restores the real main.ts, keeps the audit), lint, release
python3 /path/to/eol-radar/play/pack.py "$F"
rote play lint eol-radar
rote play release eol-radar                    # status: draft -> released, readiness: ready

# 4. publish, then verify from a directory that has never seen the code
rote registry play push "$F" abhay-codes07 --dry-run
rote registry play push "$F" abhay-codes07
cd /tmp && rote play run https://play.modiqo.ai/abhay-codes07/eol-radar root=/some/repo --yes
rote play pending discard eol-radar
```

Rules that bite: quote non-string defaults (`'90'`, not `90`); no literal `*/`
in the frontmatter; every step gets a `timeout_ms`; never `console.log` in a
step; a local play takes named parameters and no `--yes`, a registry play needs
`--yes`; `deps.toml` needs `schema_version = 1`; the export cannot reify a value
that also occurs elsewhere, so `horizon_days` is wired in by hand and stays so.

## Recording the evidence

After a verified local run, the fixtures and negative cases are recorded by the
audit tool, which also declares them in the frontmatter:

```bash
rote play run eol-radar root=$TARGET
play audit fixtures "$F"                        # writes resources/presentation-fixtures and resources/cases
cp -r "$F/resources/presentation-fixtures" "$F/resources/cases" /path/to/eol-radar/play/resources/
play audit rehearse "$F" --profile live,stock-macos,ubuntu-lts
```

The recorded fixtures are from GitHub's own `actions/starter-workflows`
repository, which is public and carries eight `node20` actions, so the packed
evidence tells the same story as the pitch. `tests/test_play_package.py` checks
that no fixture names a path on the author's machine.

## Test the negative space

```bash
rote play run eol-radar root=$TARGET                        # happy path, twice: it must be re-runnable
rote play run eol-radar root=/tmp/empty-dir                 # nothing found, exit 0
rote play run eol-radar root=/nonexistent                   # five scanners fail with the reason; resolve and join blocked
rote play run eol-radar root=$TARGET horizon_days=abc       # join rejects the input, says why
rote play run eol-radar root=$TARGET fail_on=dead           # the run fails and the failure carries the summary
rote play run eol-radar root=$TARGET --output=summary       # one line, same facts
rote play run eol-radar root=$TARGET --output=json          # the whole report under .report
```

All of these were run against 0.1.3 before it was pushed. The offline suite
covers the same ground without rote: `python3 -m unittest discover -s tests`.

## Schedule it

Explicitly rewarded, and this Play is built for it: the countdown to
23 September moves every day.

```bash
play recurring probe
play recurring schedule --reference abhay-codes07/eol-radar@0.1.3 --cadence daily --for 6d \
  --parameter root=/path/to/your/repo --cwd /path/to/your/repo \
  --why "Daily death calendar for my own repo: counts down to the Node 20 runner removal on 2026-09-23"
play recurring list
```

## The wrong turn to keep

The first version of the CI scanner treated `actions/checkout@v4` as current
because v4 is the latest major. It is not current in the way that matters: its
`action.yml` declares `runs.using: node20`, and GitHub removes Node 20 from the
runners on 23 September. The fix was to read `action.yml` at the exact ref
instead of trusting the tag. That correction is the insight the whole tool
rests on.

The second wrong turn is this document's previous edition: shipping a Play that
cloned its own code at run time. It worked, and it was the wrong shape for a
tool that asks to be trusted with a repository.
