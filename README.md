# goodfood-build-farm

Public GitHub Actions runners for the private `Mom-s-Food/goodfood-main` repository. A public
repository gets unlimited Actions minutes; this one holds **no application source**, only
workflows. Every job checks the private source out with a token, never keeps the credential,
and works on one pinned commit.

| Workflow | Trigger | Does |
| --- | --- | --- |
| `farm-ci.yml` | `repository_dispatch: trigger-ci`, or by hand with a commit SHA | fmt, clippy `-D warnings`, nextest with a PostgreSQL service, doc tests, contract drift, eval dataset check, cargo-deny and cargo-audit, script tests, infra consistency and links. Reports `farm/ci` as a commit status on the private commit. |
| `farm-perf.yml` | By hand with a commit SHA, and weekly on `main` (Mondays 06:17 UTC) | Lane T8: the Rust budget tests beside the code (`req-coverage.py --lane-tests T8`) with `GOODFOOD_PERF=1` in a release build, on the same tmpfs PostgreSQL as `farm-ci`. Uploads the `t8-timing-report` artifact (every budget's measured value against its target, the runner's CPU) and reports `farm/perf` on the private commit; it never touches `farm/ci`. |
| `farm-android.yml` | `repository_dispatch: trigger-ci` (with farm-ci), or by hand with a commit SHA, `task=record` and a branch, or `task=apk` and a commit SHA | The Android checks (goodfood-main A1): module graph, ktlint, detekt, Android Lint, unit and Room tests, Roborazzi screenshots with accessibility checks, committed Room schemas, the 30 MB download budget. Runs only when the commit changes `android/`, `contracts/`, `design/tokens/` or `tests/fixtures/sync/`; reports `farm/android` on the private commit, never `farm/ci`. `task=record` formats, exports the Room schemas and records the screenshot baselines on this Linux runner and pushes them to the branch (never `main`) as one commit that changes only `android/`. `task=apk` with a commit SHA builds the staging debug APK for a real phone, scans it for credential shapes and keeps it as a one-day artifact (a public repository's artifacts are open to any signed-in GitHub user); it reports no status. |
| `farm-ios.yml` | `repository_dispatch: trigger-ci` (with farm-ci), or by hand with a commit SHA or `task=record` and a branch | The iOS checks (goodfood-main I1) on one `macos-26` job, all through `ios/scripts/ci.sh`: swift-format and SwiftLint, the XcodeGen project with the committed `Package.resolved` pins, an unsigned simulator build, Swift Testing (T1, T2 on GRDB) and snapshot tests, and the Maestro smoke journeys against the contract mock server. Runs only when the commit changes `ios/`, `contracts/{openapi,analytics,fixtures}/`, `design/tokens/`, `tests/fixtures/sync/` or `tests/mobile-e2e/{ios,shared}/`; reports `farm/ios` on the private commit, never `farm/ci`. `task=record` formats, resolves `Package.resolved` and records the snapshot baselines on the same runner and pushes them to the branch (never `main`) as one commit that changes only `ios/`. |
| `farm-build.yml` | `repository_dispatch: trigger-build`, or by hand | Builds the `api` and `worker` images (arm64, for the OCI Ampere nodes) and pushes them to `ghcr.io/mom-s-food/goodfood-main/{api,worker}:<sha>`; uploads a release receipt with each digest. It never deploys. |
| `farm-evals.yml` | By hand: `branch`, `mode` (`live` or `record`), `suite`, case and cost caps | Runs the evals command with the development key. `record` writes each answer to `backend/testdata/recordings/` and pushes one commit to the branch (never `main`). |
| `farm-task.yml` | By hand: `branch` (never `main`), `task`, optional `suite` | One file-writing cargo task, pushed to the branch as one commit: `gen-cases` (the eval case generator; commits `evals/`), `write-recordings` (the synthetic recordings of the held-out split on the default grid, no key and no model call; commits `backend/testdata/recordings/` and `evals/`) or `fmt` (`cargo fmt --all`; commits `backend/`). A task that changes anything outside its paths fails and pushes nothing. |
| `farm-zap.yml` | Nightly (02:40 UTC) on `main`, or by hand with a commit SHA | Lane T9: `scripts/test/security-scan.sh --dast --env staging`, the OWASP ZAP API scan of staging from the contract (ZAP pinned by digest in that script), signed in as a fresh synthetic user that is deleted afterwards. The log shows only alert counts per level (`--public-log`: no alert names, plugin ids, URLs or ZAP output), and the `zap-report-<run>` artifact holds only `zap-reports.tar.gz.age`, the JSON and HTML reports and ZAP's output encrypted with `age` to the owner's public key in the `ZAP_REPORT_AGE_RECIPIENT` repository variable (none kept until it exists); a High-risk alert fails the run unless `tests/security/zap/rules.tsv` says otherwise. Needs the `QA_SIGNIN_KEY` secret (B9 decision 2); until it exists the run warns and scans nothing. Never scans production. |
| `farm-synthetic.yml` | By hand with a commit SHA | B9 Tasks 1 and 2: starts the local stack (Docker Compose) from the api and worker images staging pins at that commit, seeds three synthetic users through the public API (`tests/synthetic`), seeds them again (all skipped), then runs `tests/synthetic/driver.js` in smoke mode for `driver_duration` (default 2 minutes) with the k6 of the image staging pins. Uses no key: the local stack serves its own sign-in codes. |
| `farm-probe.yml` | Always: a chain of ~5 h 40 min runs, each dispatching the next; an hourly schedule only restarts a broken chain; or by hand with `probes` for a one-off | B9 Task 5, the external probe: `scripts/validation/probe.sh --env staging` signs a synthetic user in, logs a typed meal, reads Today and deletes the meal, each step within its budget plus 170 ms, at every 5-minute mark. GitHub's cron is best effort (a `*/5` schedule started no run in over two hours on 2026-10-31), so the workflow is its own clock and holds one runner all the time. Each run keeps `probe-<run>` (`results.jsonl`, statuses and timings only) for 30 days; `scripts/probe-window.py` counts the 7-day window from them. Two failed probes in a row fail the run, so GitHub mails the owner. Takes `probe.sh` from `PROBE_DEFAULT_REF` until the private `main` contains it. Disable the workflow to stop it. |
| `farm-lint.yml` | Every push to this repository's `main` that changes `.github/` or `scripts/`, or by hand | actionlint over the farm's workflows and `scripts/check-farm-workflows.py` (the job budget above). Reads only this repository. |

The private repository sends both dispatches on every push to `main`
(`.github/workflows/farm-dispatch.yml` there).

## Job budget

The org is on GitHub Free: about **20 concurrent jobs for the whole org**, shared by every
workflow here. `.github/farm-capacity.json` is the budget and `farm-lint.yml` enforces it on every
push (`scripts/check-farm-workflows.py`, plus actionlint):

- every matrix has a `max-parallel` of at most 4 (farm-fuzz: 2 builds, 4 targets at once);
- every background workflow (fuzz, perf, replay per mode, synthetic, load, evals, zap,
  staging-nightly, probe) runs **one at a time** through a fixed concurrency group with
  `cancel-in-progress: false`: a newer dispatch replaces the queued one, a running one is never
  cancelled. Their peaks add up to 13 jobs, so farm-ci (6 jobs at once after setup) always has 7;
- daily schedules are at least an hour apart (UTC): zap 02:40, perf 06:17 (Mondays),
  staging-nightly 07:17, replay 19:50, fuzz 22:23; the probe holds 1 job all the time (its chain);
- farm-build's `deploy-staging` never waits on a runner: while `farm/ci` is pending it posts
  `farm/deploy` = pending ("waiting for farm/ci") and ends, and farm-ci's report re-runs that job
  once `farm/ci` passes. `farm/deploy` on the private commit shows where the deploy stands.

For agents that dispatch farm runs:

- Dispatch with the output of `git rev-parse <branch>`, never a typed SHA; farm-ci now fails at
  once on a SHA that does not exist.
- Dispatch farm-ci once per pushed SHA and **do not cancel a queued run**: it holds no job slot,
  and cancelling it only loses its place. Cancel only a run superseded by a newer commit on the
  same branch. A cancelled run posts `farm/ci` = error ("cancelled; re-dispatch farm-ci") while
  the status still points at it, so a commit never stays pending.
- During the day, run farm-fuzz with `targets` for a subset rather than every target.

## Secrets

| Where | Secret | What it needs |
| --- | --- | --- |
| this repository | `PRIVATE_REPO_PAT` | Read `Mom-s-Food/goodfood-main` contents, write its commit statuses, and push its GHCR packages. A classic token with `repo` and `write:packages`, from a machine user if possible, because fine-grained tokens can't push packages yet. |
| this repository | `QA_SIGNIN_KEY` | The staging qa-replay key, for `farm-probe.yml`, `farm-zap.yml`, the farm load runs and the nightly staging T4 journeys. Owner-gated (B9 decision 2): set only after the owner approves, piped from `.env` with `gh secret set` (goodfood-main `docs/operations/SECRETS.md`). Staging-only; it reads nothing but synthetic users' sign-in codes. |
| this repository (variable, not a secret) | `ZAP_REPORT_AGE_RECIPIENT` | The owner's `age` X25519 public key (`age1...`) that `farm-zap.yml` encrypts its reports to. The owner makes the key pair with `age-keygen` and keeps the identity file off the farm; the public key is not a credential. Without it no ZAP report is kept. |
| `goodfood-main` | `FARM_DISPATCH_TOKEN` | Send `repository_dispatch` to this repository: a fine-grained token on this repository with Contents read and write. |

## Rules

- Logs are public. Nothing prints a secret, and all test data is synthetic. A compiler error
  quotes the failing source lines; the owner accepted that on 2026-11-03.
- Only `repository_dispatch`, `workflow_dispatch`, `farm-lint`'s push trigger (this repository's own files, no secret), `farm-perf`'s weekly schedule (which
  measures `main`) `farm-zap`'s nightly schedule (which scans staging from `main`) and `farm-probe`'s hourly backstop schedule and self-dispatched chain (which probe staging with the pinned or `main`'s `probe.sh`) start a run. Pull requests from forks
  never do, so outside code never runs with the token.
- Deploying to OCI is a later step that needs the owner's approval.
