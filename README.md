# EOL Radar

**Everything in your repository with a death date, sorted by what breaks first.**

Point it at a repo. It reads what you actually declare — runtime pins, Docker
base images, GitHub Actions runners and actions, serverless runtimes, lockfile
dependencies — looks each one up against public lifecycle data, and prints one
ordered timeline: what is already dead, what dies next, and the date it takes
your CI or your deploy with it.

Read-only. No credentials. No uploads. Nothing leaves your machine except
requests to public APIs.

```
$ python scripts/eol_radar.py --root .

EOL Radar · my-service · checked 2026-09-04 · horizon 90 days
=============================================================

  3 dead · 2 dying · 1 watch · 24 ok

DEAD — already out of support (3)
------------------------------------------------------------------------------
  python:3.9-slim-bookworm
    Dockerfile:8  died 2025-10-31 (308 days ago)
    why: python 3.9 end of life 2025-10-31
    fix: move to python 3.13

  ubuntu-20.04
    .github/workflows/ci.yml:9  died 2025-04-15 (507 days ago)
    why: github-actions-runner-images ubuntu-20.04 end of life 2025-04-15
    fix: upgrade to ubuntu-24.04

  request@2.88.2
    package.json:9
    why: deprecated by its publisher: request has been deprecated, see
         https://github.com/request/request/issues/3142
    fix: replace this dependency

DYING — loses support inside the horizon (2)
------------------------------------------------------------------------------
  actions/checkout@v4
    .github/workflows/ci.yml:14  breaks 2026-09-23 (in 19 days)
    why: GitHub Actions runners remove Node 20 on 2026-09-23
    note: read from https://raw.githubusercontent.com/actions/checkout/v4/action.yml
    fix: upgrade to actions/checkout@v5 (runs on node24)
```

That `actions/checkout@v4` line is the one people do not expect. The tag looks
current. The action still declares `runs.using: node20`, and GitHub removes
Node 20 from the runners on **23 September 2026**, so it stops starting. EOL
Radar reads the `action.yml` at the ref you pinned instead of trusting the tag.

---

## Why this exists

Lifecycle information is everywhere and useless where you need it. endoflife.date
knows every date but nothing about your repo. Your scanner knows your
dependencies but not that your runner image retires next month. Nobody joins the
two, so the deadline arrives as a red build.

Five things in a repo carry a death date. EOL Radar reads all five:

| Surface | What it reads | What it catches |
|---|---|---|
| **runtimes** | `.nvmrc`, `.python-version`, `.tool-versions`, `mise.toml`, `engines`, `requires-python`, `go.mod`, `runtime.txt`, `global.json`, `Gemfile` | the language version you pinned is out of support |
| **containers** | `Dockerfile`, `docker-compose*.yml`, `devcontainer.json` | a dead base image, and the dead distro layer inside it (`python:3.9-slim-bookworm` is two findings) |
| **ci** | `.github/workflows/*.yml`, local `action.yml`, `.gitlab-ci.yml` | retired runner labels, actions whose real runtime is `node20`, toolchain versions requested via `setup-*` |
| **cloud** | Serverless, SAM/CloudFormation, Terraform, CDK, App Engine, Vercel, Netlify | a Lambda runtime that stops accepting updates, a Node version your host is about to disable |
| **packages** | npm, PyPI, Cargo, Go, RubyGems, Composer manifests and lockfiles | publisher-deprecated versions, archived upstreams, frameworks on a dead major |

## Install

Nothing to install. Python 3.8 or newer, standard library only. `git` is needed
only for `--repo`.

```bash
git clone https://github.com/abhay-codes07/eol-radar.git
cd eol-radar
python scripts/eol_radar.py --root /path/to/your/repo
```

## Usage

```
python scripts/eol_radar.py [options]

  --root PATH          repository to scan (default: .)
  --repo OWNER/NAME    public GitHub repository to shallow-clone and scan
  --user NAME          scan every repository of a GitHub user or org and roll
                       the results into one account-wide view
  --max-repos N        how many repositories --user scans (10)
  --include-forks      include forks, which --user skips by default
  --horizon DAYS       anything expiring within this window counts as dying (90)
  --no-packages        skip the dependency surface (fastest)
  --max-packages N     cap on package versions queried (300)
  --baseline FILE      previous --output json result, to show what changed
  --fail-on WHAT       none | dying | dead   exit 2 when matched (none)
  --output VIEW        human | summary | json | patch (human)
  --depth N            directory depth to search (8)
  --today YYYY-MM-DD   evaluate against a fixed date (for tests)
  --keep-work          keep the intermediate step files and print their path
  --no-cache           ignore the on-disk HTTP cache
```

Scan somebody else's repository without cloning it yourself:

```bash
python scripts/eol_radar.py --repo actions/checkout
```

### Your whole account at once

One repository tells you what to fix. A whole account tells you what to fix
**first**, because the same date usually takes out several repositories and one
upgrade usually clears most of them.

```bash
python scripts/eol_radar.py --user your-github-name --max-repos 20
```

```
SHARED DEADLINES — one date, several repositories
------------------------------------------------------------------------------
  2026-09-23  in 19 days     31 finding(s) across 5 repositories
      GitHub Actions runners remove Node 20 on 2026-09-23
      Agent-DNA-Transfer, VOLO, assay, lethe, opentelemetry-kpt-demo

ONE FIX, MANY REPOSITORIES — ranked by reach
------------------------------------------------------------------------------
  actions/checkout@v4                          4 repo(s)
      upgrade to actions/checkout@v5 (runs on node24)
  nodejs 20 (engines.node)                     4 repo(s)
      move to nodejs 24 (current LTS)
```

### The mechanical half of the fix, as a patch

`--output patch` prints a unified diff for the changes it can make safely, and
writes nothing itself. Every replacement is verified first: an action upgrade
only appears once that release's own `action.yml` has been read and confirmed to
run on `node24`, and a runner label only appears if endoflife.date still lists
it as maintained.

```bash
python scripts/eol_radar.py --output patch > fix.patch
git apply fix.patch
python scripts/eol_radar.py            # the same findings are now gone
```

```diff
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
-    runs-on: ubuntu-20.04
+    runs-on: ubuntu-24.04
-      - uses: actions/checkout@v4
-      - uses: actions/setup-node@v4
+      - uses: actions/checkout@v5
+      - uses: actions/setup-node@v5
```

Runtime pins, base images and dependencies are reported but never rewritten.
Those need a human decision, so putting them in a patch would be dishonest.

Watch what changes, which is the point of running it more than once:

```bash
python scripts/eol_radar.py --output json > yesterday.json
# tomorrow
python scripts/eol_radar.py --baseline yesterday.json
```

```
SINCE LAST RUN (2026-09-04)
------------------------------------------------------------------------------
  + new     DYING  actions/setup-node@v4  (.github/workflows/ci.yml:15)
  - gone    ubuntu-20.04  (.github/workflows/ci.yml:9)
  ~ moved   python 3.10  WATCH -> DYING
```

Gate a pipeline on it:

```bash
python scripts/eol_radar.py --fail-on dying   # exit 2 if anything dies within the horizon
```

## Verdicts

| | meaning |
|---|---|
| `DEAD` | end of life has passed, or the package is deprecated, or its upstream is archived |
| `DYING` | end of life or a platform cut-off lands inside `--horizon` |
| `WATCH` | active support has ended, or end of life is within a year |
| `OK` | supported |
| `UNKNOWN` | nothing authoritative was found, and nothing was guessed |

`UNKNOWN` is a real answer. An unrecognised base image or an unreachable API is
reported as unknown rather than filled in with a plausible date.

## Where the dates come from

Every source is public and keyless.

| Source | Used for |
|---|---|
| [endoflife.date](https://endoflife.date) | release cycles, end-of-life and end-of-active-support dates for 470 products |
| [deps.dev](https://deps.dev) (Google) | per-version deprecation status and the upstream repository |
| [registry.npmjs.org](https://registry.npmjs.org) | the npm deprecation message |
| raw.githubusercontent.com | an action's `action.yml` at the exact ref, for `runs.using` |
| api.github.com | whether the upstream repository is archived |

Plus one small [curated table](data/enforcement.json) of platform cut-offs that
no feed models, because they belong to a platform rather than to a release line:
the GitHub Actions Node 20 removal, Vercel disabling Node 20, the Lambda
`python3.9` create block, and Dependabot dropping Python 3.9. Each rule carries
the primary source it was read from and the date it was verified. Everything
that can come from a live feed does.

`GITHUB_TOKEN` is optional and never required. Without it the archived-upstream
check runs inside the anonymous 60-requests-per-hour budget and says so in the
ledger when it stops early.

## Reading the ledger

Every run ends with a ledger saying what each source did.

```
LEDGER
------------------------------------------------------------------------------
  ok         scan: ci                    11 item(s) from 1 file(s)
  skipped    scan: cloud                 no deployment manifests found
  ok         endoflife.date              release cycles for 9 products
  degraded   deps.dev + npm              deprecation status for 7 package versions
```

A surface that finds nothing is `skipped`, not an error. A source that fails is
`degraded` and its rows come back `UNKNOWN`. One unreachable API never takes the
report down with it.

## How it is put together

```
scan_runtimes ─┐
scan_containers┤
scan_ci        ├─> resolve ─> join ─> human | summary | json | patch
scan_cloud     ┤
scan_packages ─┘

--user: the whole pipeline per repository ─> aggregate ─> account view
```

The five scanners are independent, read only the filesystem, and never touch the
network, which is what makes them reproducible and parallel. `resolve.py` is the
only step that makes a request. `join.py` applies the verdicts and renders. Each
step is a process that prints one JSON object, so any of them can be run and
inspected on its own:

```bash
python scripts/scan_ci.py --root . | python -m json.tool
```

## Tests

```bash
python -m unittest discover -s tests -v
```

55 tests, no network, fixed clock. The verdict engine is tested against a stub
fact set so the expected output does not change when endoflife.date publishes a
new release. Verified on Python 3.10 (Windows) and 3.12 (Linux).

## Limits, stated plainly

- YAML is read with a small line scanner, not a parser, because the tool depends
  on the standard library only. Keys that are plain scalars or one-line flow
  lists are read correctly; anchors, merge keys and multi-line block scalars are
  skipped rather than guessed at.
- A version range such as `^20.1.0` is evaluated at its lowest allowed release,
  and the report says so.
- The alias table that maps image names to lifecycle products is best-effort. A
  miss produces `UNKNOWN`, never a wrong date.
- Composer packages get framework lifecycle data but no deprecation check, since
  deps.dev does not cover Packagist.

## The Play

EOL Radar is built to be published as a [rote](https://www.modiqo.ai) Play, so
it can be run from any agent harness with fresh inputs and inspected before it
runs. See [docs/PLAY.md](docs/PLAY.md).

## Licence

MIT. See [LICENSE](LICENSE).

Lifecycle dates are provided by endoflife.date and its contributors, deps.dev,
and the npm registry. This tool reads them; it does not republish them.
