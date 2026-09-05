# Publishing EOL Radar as a rote Play

Published on 6 September 2026 as `abhay-codes07/eol-radar@0.1.2`:
https://play.modiqo.ai/abhay-codes07/eol-radar. The procedure below is how it
was done and how a version bump is done again.

This is the submission procedure. Publishing to Community **is** the entry; there
is no separate form. Everything here runs in **WSL2 Ubuntu**, because rote and
play support macOS and Linux only.

## Before you start: two facts that decide whether a stranger's run works

**The repository must be public.** The Play's first step fetches the scanner
with a shallow clone at a pinned tag. A judge or another competitor running the
Play has no access to a private repository, so their run would fail at step one.

```bash
gh repo edit abhay-codes07/eol-radar --visibility public --accept-visibility-change-consequences
```

**The tag must exist.** The Play pins `v0.1.0`, so the code it runs is
immutable and inspectable on GitHub at that exact commit.

```bash
git tag -a v0.1.0 -m "EOL Radar 0.1.0" && git push origin v0.1.0
```

## 1. Sign in and install

```bash
wsl -d Ubuntu
export PLAY_LOGIN_PROVIDER=github
curl -fsSL https://getrote.dev/playoffs/install.sh | sh
rote whoami
sudo apt-get install -y python3 git
```

Your handle becomes the Play's public namespace. Warm up if you have not:
`/play what's new`, `/play run hello`, then post `warmed up` in
[Discord](https://discord.gg/YyjBtzvhGz).

## 2. The Play's shape

Five parameters, all plain values, none conditional:

| parameter | type | default | meaning |
|---|---|---|---|
| `root` | string | `.` | repository to scan |
| `horizon_days` | integer | `'90'` | expiring inside this window counts as dying |
| `max_packages` | integer | `'300'` | cap on package versions queried |
| `baseline` | string | `''` | previous JSON result, for a since-last-run diff |
| `fail_on` | string | `'none'` | `none`, `dying` or `dead`: exit 2 when matched |

Eight steps in four layers. The scanners are independent, read only the
filesystem, and run in parallel; only `resolve` touches the network.

```
fetch_tool ─┬─> scan_runtimes ──┐
            ├─> scan_containers ┤
            ├─> scan_ci         ├─> resolve ─> join
            ├─> scan_cloud      ┤
            └─> scan_packages ──┘
```

## The finished Play is in this repository

`play/main.ts` and `play/deps.toml` are the Play exactly as published: the
frontmatter with ten steps in six layers, parameter defaults, timeouts, and a
presentation that renders the report. Sections 3 and 4 describe how it was
produced, and what had to be corrected by hand afterwards, because the exported
draft was an honest recording but not yet a correct Play:

- every step came out as a root with no edges, so `depends_on` was added;
- `90` stayed a literal, so `horizon_days` was wired in;
- a plain `git clone` refused to run a second time in the same workspace, so
  fetching became `git init`, `git fetch <tag>`, `git checkout FETCH_HEAD`,
  which is idempotent by construction;
- the generated presentation printed "Rendered 8 step(s)", so it was rewritten
  to render the report.

To install it locally without re-capturing: copy both files into the package
directory `rote play info eol-radar` reports, then lint.

## 3. Capture

Start the journey viewer first so the exploration is on record:

```bash
play journey view --active
```

Open a workspace and run each step as its own capture, against a real
repository (one of the ones from the sweep, not the fixture). One reading is one
step: that is what lets the five scanners become five parallel root steps.

```bash
rote init eol-radar --seq --force
cd "$HOME/.rote/workspaces/eol-radar"          # proc run only works from inside the workspace
# Two real targets are already staged in WSL. starter-workflows is GitHub's own
# template repository and carries eight node20 actions, which makes the capture
# tell its own story; assay is one of yours.
export TARGET=/home/abhay/eol-targets/starter-workflows
rote workspace set root=$TARGET horizon_days=90 max_packages=300 fail_on=none   # these become the parameters

rote proc run git init -q eol-radar-tool
rote proc run git -C eol-radar-tool fetch -q --depth 1 https://github.com/abhay-codes07/eol-radar.git v0.1.1
rote proc run git -C eol-radar-tool checkout -q --force FETCH_HEAD

rote proc run python3 eol-radar-tool/scripts/scan_runtimes.py   --root $TARGET --out work/runtimes.json
rote proc run python3 eol-radar-tool/scripts/scan_containers.py --root $TARGET --out work/containers.json
rote proc run python3 eol-radar-tool/scripts/scan_ci.py         --root $TARGET --out work/ci.json
rote proc run python3 eol-radar-tool/scripts/scan_cloud.py      --root $TARGET --out work/cloud.json
rote proc run python3 eol-radar-tool/scripts/scan_packages.py   --root $TARGET --max 300 --out work/packages.json

rote proc run python3 eol-radar-tool/scripts/resolve.py work/runtimes.json work/containers.json work/ci.json work/cloud.json work/packages.json --out work/facts.json

rote proc run python3 eol-radar-tool/scripts/join.py work/runtimes.json work/containers.json work/ci.json work/cloud.json work/packages.json --facts work/facts.json --root $TARGET --horizon 90 --fail-on none --baseline "" --output json
```

Anchor it before anything can interrupt:

```bash
rote play pending write eol-radar \
  --name eol-radar \
  --description "One job: what in this repository stops working, and on what date. Reads runtime pins, base images, CI runners and actions (resolved to the Node runtime their action.yml really declares, so SHA pins are caught), cloud runtimes and packages, then prints everything with a death date, soonest first, with the exact version to move to. Outdated is not the same as dead: this is about dates a platform enforces, not the newest release. Read-only, no credentials." \
  --notes "fetch the scanner at a pinned tag, five independent filesystem scans in parallel, one network resolve, one join"
```

### The wrong turn to keep

Keep it in the journey, on purpose. The first version of the CI scanner treated
`actions/checkout@v4` as current because v4 is the latest major. It is not
current in the way that matters: its `action.yml` declares `runs.using: node20`,
and GitHub removes Node 20 from the runners on 23 September. The fix was to read
`action.yml` at the exact ref instead of trusting the tag. That correction is
the insight the whole tool rests on, and a spotless run reads as a demo.

## 4. Crystallise

```bash
rote workspace export main.ts --params root,horizon_days,max_packages,fail_on -d "..."
rote play info eol-radar                      # prints the package directory it landed in
rote play run https://play.modiqo.ai/modiqo/play-dag play=<that directory>/main.ts --yes
rote play lint eol-radar
```

The DAG check should show 10 steps and 6 layers. `8 steps · 1 layers` with
"(no edges)" is what the raw export produces; that is the moment to replace it
with `play/main.ts` from this repository, or add the `depends_on` edges by hand.
`deps.toml` needs `schema_version = 1` at the top for this rote version.

Rules that bite: quote non-string defaults (`'90'`, not `90`); a value-edge jq
must resolve to a scalar; no literal `*/` in the frontmatter; every step gets a
`timeout_ms`, and `scan_packages` plus `resolve` deserve more than the default
30 seconds; never `console.log` in a step. `deps.toml` in this repository
already declares exactly `python3` and `git`.

## 5. Test the negative space

A local play takes named parameters and no `--yes`; that flag is for registry
plays only.

```bash
rote play run eol-radar root=$TARGET                              # happy path, twice: it must be re-runnable
rote play run eol-radar root=/tmp/empty-dir                       # nothing found, exit 0
rote play run eol-radar root=/nonexistent                         # hard fault, clear message
rote play run eol-radar root=$TARGET horizon_days=abc             # rejected input
rote play run eol-radar root=$TARGET fail_on=dead                 # the run fails and says why
rote play run eol-radar root=$TARGET --output=summary             # one line, same facts
rote play run eol-radar root=$TARGET --output=json                # the whole report under .report
```

The offline suite covers the same ground without rote:
`python3 -m unittest discover -s tests`.

## 6. Publish

```bash
rote play release eol-radar
rote registry play push <package directory> abhay-codes07
```

Choose **Community** when asked where the method should live. Then verify it
the way a judge will, from a directory that has never seen the code:

```bash
cd /tmp && rote play run https://play.modiqo.ai/abhay-codes07/eol-radar root=/some/repo --yes
rote play pending discard eol-radar
```

Versions are immutable. Every fix afterwards is a bump.

## 7. Schedule it

Explicitly rewarded, and this Play is built for it: the countdown to
23 September moves every day and `baseline` reports what changed.

```bash
play recurring probe
play recurring schedule --reference abhay-codes07/eol-radar@0.1.2 \
  --cadence daily \
  --why "Daily death calendar for my repos; counts down to the Node 20 runner removal" \
  --for 6d
```
