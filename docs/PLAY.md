# Publishing EOL Radar as a rote Play

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

## 3. Capture

Start the journey viewer first so the exploration is on record:

```bash
play journey view --active
```

Open a workspace and run each step as its own capture, against a real
repository (one of the ones from the sweep, not the fixture). One reading is one
step: that is what lets the five scanners become five parallel root steps.

```bash
rote init eol-radar --seq
export TARGET=/path/to/a/real/repo

rote proc run git clone --depth 1 --branch v0.1.0 https://github.com/abhay-codes07/eol-radar.git eol-radar-tool

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
  --description "Everything in a repo with a death date, soonest first: end-of-life runtimes, base images, CI runners and actions resolved to the Node runtime they really declare, cloud runtimes, deprecated packages. Read-only, no credentials." \
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
rote workspace export eol-radar --params root,horizon_days,max_packages,baseline,fail_on
rote play run https://play.modiqo.ai/modiqo/play-dag play=./main.ts
rote play lint main.ts
```

The DAG check should show 8 steps and 4 layers. `1 step · 1 layer` means the
capture collapsed and should be redone.

Rules that bite: quote non-string defaults (`'90'`, not `90`); a value-edge jq
must resolve to a scalar; no literal `*/` in the frontmatter; every step gets a
`timeout_ms`, and `scan_packages` plus `resolve` deserve more than the default
30 seconds; never `console.log` in a step. `deps.toml` in this repository
already declares exactly `python3` and `git`.

## 5. Test the negative space

```bash
rote play run main.ts root=$TARGET                                # happy path
rote play run main.ts root=/tmp/empty-dir                         # nothing found, exit 0
rote play run main.ts root=/nonexistent                           # hard fault, clear message
rote play run main.ts root=$TARGET horizon_days=abc               # rejected input
rote play run main.ts root=$TARGET fail_on=dead                   # exit 2 if anything is dead
rote play run main.ts root=$TARGET --output=summary               # one line, same facts
rote play run main.ts root=$TARGET --output=json > base.json
rote play run main.ts root=$TARGET baseline=base.json             # since-last-run section
```

The offline suite covers the same ground without rote:
`python3 -m unittest discover -s tests`.

## 6. Publish

```bash
rote play release eol-radar
rote registry play push main.ts <handle>
```

Choose **Community** when asked where the method should live. Then verify it
the way a judge will, from a directory that has never seen the code:

```bash
cd /tmp && rote play run https://play.modiqo.ai/<handle>/eol-radar root=/some/repo --yes
rote play pending discard eol-radar
```

Versions are immutable. Every fix afterwards is a bump.

## 7. Schedule it

Explicitly rewarded, and this Play is built for it: the countdown to
23 September moves every day and `baseline` reports what changed.

```bash
play recurring probe
play recurring schedule --reference <handle>/eol-radar@0.1.0 \
  --cadence daily \
  --why "Daily death calendar for my repos; counts down to the Node 20 runner removal" \
  --for 6d
```
