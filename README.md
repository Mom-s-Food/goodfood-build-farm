# goodfood-build-farm

Public GitHub Actions runners for the private `Mom-s-Food/goodfood-main` repository. A public
repository gets unlimited Actions minutes; this one holds **no application source**, only
workflows. Every job checks the private source out with a token, never keeps the credential,
and works on one pinned commit.

| Workflow | Trigger | Does |
| --- | --- | --- |
| `farm-ci.yml` | `repository_dispatch: trigger-ci`, or by hand with a commit SHA | fmt, clippy `-D warnings`, nextest with a PostgreSQL service, doc tests, contract drift, eval dataset check, cargo-deny and cargo-audit, script tests, infra consistency and links. Reports `farm/ci` as a commit status on the private commit. |
| `farm-perf.yml` | By hand with a commit SHA, and weekly on `main` (Mondays 06:17 UTC) | Lane T8: the Rust budget tests beside the code (`req-coverage.py --lane-tests T8`) with `GOODFOOD_PERF=1` in a release build, on the same tmpfs PostgreSQL as `farm-ci`. Uploads the `t8-timing-report` artifact (every budget's measured value against its target, the runner's CPU) and reports `farm/perf` on the private commit; it never touches `farm/ci`. |
| `farm-build.yml` | `repository_dispatch: trigger-build`, or by hand | Builds the `api` and `worker` images (arm64, for the OCI Ampere nodes) and pushes them to `ghcr.io/mom-s-food/goodfood-main/{api,worker}:<sha>`; uploads a release receipt with each digest. It never deploys. |
| `farm-evals.yml` | By hand: `branch`, `mode` (`live` or `record`), `suite`, case and cost caps | Runs the evals command with the development key. `record` writes each answer to `backend/testdata/recordings/` and pushes one commit to the branch (never `main`). |
| `farm-task.yml` | By hand: `branch` (never `main`), `task`, optional `suite` | One file-writing cargo task, pushed to the branch as one commit: `gen-cases` (the eval case generator; commits `evals/`), `write-recordings` (the synthetic recordings of the held-out split on the default grid, no key and no model call; commits `backend/testdata/recordings/` and `evals/`) or `fmt` (`cargo fmt --all`; commits `backend/`). A task that changes anything outside its paths fails and pushes nothing. |
| `farm-zap.yml` | Nightly (02:40 UTC) on `main`, or by hand with a commit SHA | Lane T9: `scripts/test/security-scan.sh --dast --env staging`, the OWASP ZAP API scan of staging from the contract (ZAP pinned by digest in that script), signed in as a fresh synthetic user that is deleted afterwards. The log shows only alert counts per level (`--public-log`: no alert names, plugin ids, URLs or ZAP output), and the `zap-report-<run>` artifact holds only `zap-reports.tar.gz.age`, the JSON and HTML reports and ZAP's output encrypted with `age` to the owner's public key in the `ZAP_REPORT_AGE_RECIPIENT` repository variable (none kept until it exists); a High-risk alert fails the run unless `tests/security/zap/rules.tsv` says otherwise. Needs the `QA_SIGNIN_KEY` secret (B9 decision 2); until it exists the run warns and scans nothing. Never scans production. |
| `farm-synthetic.yml` | By hand with a commit SHA | B9 Tasks 1 and 2: starts the local stack (Docker Compose) from the api and worker images staging pins at that commit, seeds three synthetic users through the public API (`tests/synthetic`), seeds them again (all skipped), then runs `tests/synthetic/driver.js` in smoke mode for `driver_duration` (default 2 minutes) with the k6 of the image staging pins. Uses no key: the local stack serves its own sign-in codes. |
| `farm-probe.yml` | Every 5 minutes, or by hand with a commit SHA | B9 Task 5, the external probe: `scripts/validation/probe.sh --env staging` from the private `main` signs a synthetic user in, logs a typed meal, reads Today and deletes the meal, each step within its budget plus 170 ms. Each run keeps `probe-<run>` (statuses and timings only) for 30 days; two failed probes in a row fail the run, so GitHub mails the owner. Needs the `QA_SIGNIN_KEY` secret (B9 decision 2); until it exists, or until `probe.sh` is on `main`, each run records `skipped` and passes. |

The private repository sends both dispatches on every push to `main`
(`.github/workflows/farm-dispatch.yml` there).

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
- Only `repository_dispatch`, `workflow_dispatch`, `farm-perf`'s weekly schedule (which
  measures `main`) `farm-zap`'s nightly schedule (which scans staging from `main`) and `farm-probe`'s 5-minute schedule (which probes staging with `main`'s `probe.sh`) start a run. Pull requests from forks
  never do, so outside code never runs with the token.
- Deploying to OCI is a later step that needs the owner's approval.
