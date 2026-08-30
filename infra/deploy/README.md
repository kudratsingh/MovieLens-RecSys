# Deployment scripts

What runs a release on the box, and what makes the box a box. The reasoning is
[ADR 0013](../../docs/adr/0013-production-deployment-target.md); the step-by-step is
[`docs/deployment-runbook.md`](../../docs/deployment-runbook.md). This file is the map between them.

| File | What it is |
|---|---|
| `deploy.sh` | One release, start to finish. `deploy.sh <sha>` pulls every image at that tag, runs the release jobs, brings the serving tier up, and verifies; `deploy.sh --rollback` redeploys the release recorded in `.release/previous`. Prints `DEPLOY-OK <sha>` or `ROLLBACK-OK <sha>` as its last line, and rolls back on its own when verification fails |
| `production.env.example` | The whole variable contract, one comment per variable. Copied to `/opt/movielens/.env.prod` (mode 0600) and filled in; a unit test asserts it and `docker-compose.prod.yml` never drift apart |
| `staging.env.example` | The **same** contract with staging values, for `docker-compose.staging.yml` — the overlay staging layers on the production file, so it reads the production variables exactly. Copied to `./.env.staging` and filled in. The reasoning per variable lives in `production.env.example` and is not repeated; this file says what staging sets and why that differs. The same unit test holds the two name sets equal in both directions, and asserts the placeholders are distinguishable (`REPLACE_ME__staging_*`) so a `.env.staging` that is really a copy of the production template is visible on sight |
| `provision-roles.sql` | The **one** copy of the one-time role provisioning — `app_user`, `admin_user`, `migrator`, `pgbouncer_auth` — with generated passwords passed in as psql variables. Applied by the `postgres-provision` Compose job, not by hand |
| `rollback-rehearsal.sh` | Proves the release's schema step declines to act when the database is ahead of the image running it. `make prod-rollback-rehearsal` |
| [`../host/`](../host/) | `bootstrap.sh` turns a stock Ubuntu 24.04 CX22 into this host — `deploy` user, sshd hardening, `ufw` 22/80/443, unattended upgrades, Docker CE, log rotation from `docker-daemon.json`, and the units below. Idempotent, and the only written record of the machine's configuration |
| `../host/movielens.service` | Brings the stack up at boot and stops it on shutdown. `RemainAfterExit`, so a reboot is a recovery rather than a manual restart |
| `../host/movielens-backup.{service,timer}` | Nightly 04:00 UTC: both databases dumped, `age`-encrypted, pushed off-box |
| `../host/movielens-prune.{service,timer}` | Weekly image prune, older than seven days, **never** volumes — the window is what keeps the previous release rollback-able without the registry |

Everything is driven through the Makefile's `prod-*` targets, which own the Compose invocation, the
project name and the env-file path; `deploy.sh` calls them rather than reimplementing them, so there
is one description of the release order rather than two.

```bash
make prod-deploy IMAGE_TAG=<40-character sha>   # what CI runs over SSH
make prod-rollback                              # back to .release/previous
make prod-verify                                # the post-deploy matrix, on demand
```

Staging runs the same release out of the same files, under its own Compose project, and
deliberately gets none of the three commands above — there is no `staging-deploy` and no
`staging-rollback`, because exercising the deployment's operational path against the environment
that is allowed to be broken teaches the wrong thing about both. What it has is the rehearsal:

```bash
make up-staging        # build from this checkout (or: make staging-pull IMAGE_TAG=<sha>)
make staging-release   # roles, realms, migrations, seed, materialization
make staging-serve
make staging-verify    # the same post-deploy matrix, then the reliability suite
make staging-reset     # empty volumes again, which is where every rehearsal should start
```

`docs/deployment-runbook.md`'s "Staging" section is the longer form, including what staging is
deliberately not.

Two environment switches exist for exercising this path without a registry or a box, and neither is
ever set on the host:

| Variable | Effect |
|---|---|
| `DEPLOY_DRY_RUN=1` | `deploy.sh` prints each step instead of running it. Proves the sequence and the sentinels; proves nothing about the steps |
| `DEPLOY_SKIP_PULL=1` | `prod-pull` skips the registry fetch and **only** the fetch — the assertion that every image is present at `IMAGE_TAG` still runs. For rehearsing a real `deploy.sh <sha>` against locally built images tagged by hand, for a SHA that was never pushed to GHCR |

Two properties worth keeping if you edit any of this: **no secret is ever printed** (`.env.prod` is
passed to Compose by path and never read by a script), and **every script is re-runnable** — the
bootstrap because it is the machine's only record, the provisioning SQL because that is how a
password rotation is applied, and the release path because it runs on every deploy including a
rollback.
