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

The private repository sends both dispatches on every push to `main`
(`.github/workflows/farm-dispatch.yml` there).

## Secrets

| Where | Secret | What it needs |
| --- | --- | --- |
| this repository | `PRIVATE_REPO_PAT` | Read `Mom-s-Food/goodfood-main` contents, write its commit statuses, and push its GHCR packages. A classic token with `repo` and `write:packages`, from a machine user if possible, because fine-grained tokens can't push packages yet. |
| `goodfood-main` | `FARM_DISPATCH_TOKEN` | Send `repository_dispatch` to this repository: a fine-grained token on this repository with Contents read and write. |

## Rules

- Logs are public. Nothing prints a secret, and all test data is synthetic. A compiler error
  quotes the failing source lines; the owner accepted that on 2026-11-03.
- Only `repository_dispatch`, `workflow_dispatch` and `farm-perf`'s weekly schedule (which
  measures `main`) start a run. Pull requests from forks
  never do, so outside code never runs with the token.
- Deploying to OCI is a later step that needs the owner's approval.
