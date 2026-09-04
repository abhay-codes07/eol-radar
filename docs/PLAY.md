# Publishing EOL Radar as a rote Play

The scripts in this repository are the Play. This document is the procedure for
capturing them with `rote` and publishing to Community, which is how a Rote
Playoffs entry is submitted.

Run everything below in **WSL2** (Ubuntu). `rote` and `play` support macOS and
Linux; native Windows is not supported yet.

## 1. Setup

```bash
curl -fsSL https://getrote.dev/playoffs/install.sh | sh
rote login && rote whoami
sudo apt-get install -y python3 git
```

Your handle is the Play's public namespace, so choose it deliberately. Warm up
if you have not: `/play what's new`, `/play run hello`, one more public Play,
then post `warmed up` in [Discord](https://discord.gg/YyjBtzvhGz).

## 2. Capture

Start the journey viewer first, so the exploration is on record:

```bash
play journey view --active
```

Open a workspace and run each surface as its own capture. One reading is one
step, which is what lets the five scanners become five parallel root steps
rather than one monolith.

```bash
rote init eol-radar --seq

rote proc run python3 scripts/scan_runtimes.py   --root /path/to/repo    # @1
rote proc run python3 scripts/scan_containers.py --root /path/to/repo    # @2
rote proc run python3 scripts/scan_ci.py         --root /path/to/repo    # @3
rote proc run python3 scripts/scan_cloud.py      --root /path/to/repo    # @4
rote proc run python3 scripts/scan_packages.py   --root /path/to/repo --max 300   # @5

rote proc run python3 scripts/resolve.py @1 @2 @3 @4 @5                  # @6
rote proc run python3 scripts/join.py @1 @2 @3 @4 @5 --facts @6 --horizon 90
```

Anchor the work before anything can interrupt it:

```bash
rote play pending write eol-radar \
  --name eol-radar \
  --description "Everything in a repo with a death date: end-of-life runtimes, base images, CI runners and actions, cloud runtimes, deprecated packages - sorted by what breaks first. Read-only, no credentials." \
  --notes "five independent filesystem scans fan out to endoflife.date, deps.dev, npm and raw action.yml, then one join"
```

## 3. Crystallise

```bash
rote workspace export eol-radar \
  --params root,repo,horizon_days,include_packages,max_packages,baseline,fail_on
```

Then check the shape and the contract:

```bash
rote play run https://play.modiqo.ai/modiqo/play-dag play=./main.ts
rote play lint main.ts
```

The DAG should report five root steps and at least three layers. A result of
`1 step · 1 layer` or `(no edges)` means the exploration collapsed into one
monolithic step and should be recaptured.

### Rules that bite

- Quote non-string defaults: `default: '90'`, not `default: 90`.
- A value edge's jq must resolve to a **scalar**. Pack lists with `chr(31)`.
  `fromjson`, field access, `join`, `map`, `select` and `to_entries` are
  available; `tojson` is not.
- No literal `*/` anywhere in the JSDoc frontmatter, or it terminates early.
- Every step needs `timeout_ms`. The package surface deserves more than the
  30-second default.
- Steps run without a TTY. Pass `--yes` to anything that could prompt.
- Never `console.log` in a step. Use
  `process.stdout.write(JSON.stringify(x) + "\n")`.
- Declare `python3` and `git` in `deps.toml` and nothing else. The copy in this
  repository is ready to use.

## 4. Test the negative space

Do this before a stranger does.

```bash
rote play run main.ts root=.                                  # happy path
rote play run main.ts repo=actions/checkout                   # clone path
rote play run main.ts root=/tmp/empty-dir                     # nothing found, still exits 0
rote play run main.ts root=/nonexistent                       # hard fault, clear message
rote play run main.ts 'repo=!!'                               # rejected input
rote play run main.ts root=. include_packages=false           # fast path
rote play run main.ts root=. --output=summary                 # one line, same facts
rote play run main.ts root=. --output=json > base.json
rote play run main.ts root=. baseline=base.json               # the diff section
```

The offline suite covers the same ground without rote:

```bash
python -m unittest discover -s tests -v
```

## 5. Release and publish

```bash
rote play release eol-radar
rote registry play push main.ts <your-handle>
```

Choose **Community** when Play asks where the method should live. That is the
submission; there is no separate form.

Verify it the way a judge will, from a directory that has never seen this code:

```bash
cd /tmp && rote play run https://play.modiqo.ai/<your-handle>/eol-radar root=/some/repo --yes
rote play pending discard eol-radar
```

Versions are immutable. Every fix is a bump.

## 6. Bonus laps

A schedule that catches a real change scores better than one that merely ran,
and this Play is built for exactly that: the countdown to 23 September 2026
moves every day, and `baseline` reports what appeared or disappeared.

```bash
play recurring probe
play recurring schedule --reference <your-handle>/eol-radar@0.1.0 \
  --cadence daily \
  --why "Daily death calendar for my repos; counts down to the Node 20 runner removal" \
  --for 6d
```

Keep one wrong turn visible in the journey. The honest one here is real: the
first version of the CI scanner treated `actions/checkout@v4` as current
because v4 is the latest major. It is not current in the way that matters. The
fix was to fetch `action.yml` at the ref and read `runs.using`, which says
`node20`. That correction is the expertise, and hiding it would make the
exploration look like a polished demo.

## Parameter map

The Play's parameters map one-to-one onto the scripts:

| Play parameter | Script flag |
|---|---|
| `root` | `--root` on every scanner |
| `repo` | `--repo` on the runner, which shallow-clones first |
| `horizon_days` | `--horizon` on `join.py` |
| `include_packages` | omit the `scan_packages` step |
| `max_packages` | `--max` on `scan_packages.py` |
| `baseline` | `--baseline` on `join.py` |
| `fail_on` | `--fail-on` on `join.py` |
