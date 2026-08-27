DEMO_COMPOSE = docker compose -p movielens-demo -f docker-compose.yml -f docker-compose.demo.yml
K6_VERSION := $(strip $(shell cat infra/ci/k6-version))

# --- Production-mode rehearsal stack ----------------------------------------
# A second, deliberately separate Compose project: its own name, its own
# volumes, its own images (:prod, not :demo) and no published port except the
# TLS edge, so it can run alongside the demo stack without either one touching
# the other's state. Nothing here reads docker-compose.yml -- the dev stack's
# trust authentication and published data-store ports are exactly what this
# stack exists not to inherit.
PROD_ENV_FILE ?= .env.prod
PROD_COMPOSE = docker compose -p movielens-prod -f docker-compose.prod.yml --env-file $(PROD_ENV_FILE)
# The release, verification and backup jobs sit behind the `jobs` profile so
# `up` never starts them. `run` enables a service's own profile on its own;
# `build` and `down` have to be told.
PROD_COMPOSE_ALL = $(PROD_COMPOSE) --profile jobs

# Where the load gate leaves its evidence: the k6 summary, the raw sample
# stream, the per-second latency table, and container CPU snapshots from either
# side of the measured window. CI uploads it whether the gate passed or failed,
# because a passing run is the baseline the next failure gets compared against.
# Keep the leading `./` — Compose reads a bind-mount source without one as a
# named volume.
LOAD_RESULTS_DIR ?= ./artifacts/load-smoke
# The page-shaped workloads write next to the recommendation gate's evidence
# rather than into it: two workloads, two baselines, one uploaded artifact tree.
PAGE_RESULTS_DIR ?= ./artifacts/load-pages
# Whether a breached page latency budget fails the run. Correctness is always
# enforced. PR CI leaves this false while the budgets are new (see ADR 0010's
# page-shaped note for what promoting them requires); the nightly run sets it
# true. Never flip this to false to make a red build green.
PAGE_LATENCY_ENFORCED ?= false
# Uvicorn workers on the load-serving process, in one place because the k6
# warm-up sizes itself from the same value. A warm-up that primes fewer
# processes than exist leaves cold ones inside the measured window.
API_LOAD_WORKERS ?= 4
# The gate itself lives in synthetic/load/run_gate.sh: one k6 window, the
# evidence around it, and the documented re-measure rule. It is a script rather
# than a recipe because "run, decide, maybe run once more" reads badly as
# nested make.
LOAD_GATE = API_LOAD_WORKERS=$(API_LOAD_WORKERS) K6_VERSION=$(K6_VERSION) \
	DEMO_COMPOSE="$(DEMO_COMPOSE)" sh synthetic/load/run_gate.sh

# --- Deterministic serving-artifact build -----------------------------------
# The committed bundle in infra/model-bundle/ is rebuilt and hash-compared by
# CI on linux/amd64, while most work on this project happens on arm64 macOS.
# LightGBM's text model is not byte-identical across architectures, so the
# build runs inside the features image pinned to linux/amd64 rather than
# against the host interpreter: the bundle is produced on the architecture
# that checks it. That is also why these targets do not simply invoke
# `python -m src.training.demo_artifacts` the way the other train-* targets do.
#
# ARTIFACT_AS_OF is a literal and is never computed. It becomes the manifest's
# trained_at, which is what makes manifest.json byte-stable across rebuilds,
# and it sits after the frozen persona fixture's last event so every seeded
# rating is in scope (synthetic/personas/seed.py pins both).
ARTIFACT_AS_OF := 2026-09-01T00:00:00+00:00
ARTIFACT_PLATFORM ?= linux/amd64
ARTIFACT_IMAGE ?= movielens-recsys/features:artifacts
ARTIFACT_DIR ?= infra/model-bundle
# The build reads the ratings table directly as admin_user. The defaults reach
# the demo Compose stack's Postgres; CI overrides them to reach its own.
ARTIFACT_NETWORK ?= movielens-demo_default
ARTIFACT_DB_HOST ?= postgres
ARTIFACT_DB_PORT ?= 5432
ARTIFACT_DB_NAME ?= movielens
ARTIFACT_DB_USER ?= admin_user
ARTIFACT_DB_PASSWORD ?= admin_user
ARTIFACT_TENANT ?= demo
# The thread pins repeat what the features image already bakes: this is the
# one invocation whose output is compared byte for byte, so it states its own
# conditions rather than inheriting them. PYTHONHASHSEED keeps any
# string-keyed iteration stable for the same reason. The model-server token is
# set only because the image declares ENVIRONMENT=production and Settings
# refuses its dev default there; this container never speaks to the sidecar.
# The pgBouncer admin password is here for exactly the same reason and with
# exactly as little meaning: this build talks to Postgres directly as
# admin_user and never opens the pooler's admin console. Both are non-default
# literals rather than ENVIRONMENT=dev, because disarming every production
# guard inside the one build whose point is reproducibility is the worse trade.
ARTIFACT_RUN = docker run --rm --platform $(ARTIFACT_PLATFORM) \
	--network $(ARTIFACT_NETWORK) \
	-e ADMIN_USER_DB_HOST=$(ARTIFACT_DB_HOST) \
	-e ADMIN_USER_DB_PORT=$(ARTIFACT_DB_PORT) \
	-e ADMIN_USER_DB_NAME=$(ARTIFACT_DB_NAME) \
	-e ADMIN_USER_DB_USER=$(ARTIFACT_DB_USER) \
	-e ADMIN_USER_DB_PASSWORD=$(ARTIFACT_DB_PASSWORD) \
	-e MODEL_TENANT_ID=$(ARTIFACT_TENANT) \
	-e MODEL_SERVER_AUTH_TOKEN=serving-artifact-build \
	-e PGBOUNCER_ADMIN_PASSWORD=serving-artifact-build \
	-e PYTHONHASHSEED=0 \
	-e OMP_NUM_THREADS=1 -e OPENBLAS_NUM_THREADS=1 \
	-e MKL_NUM_THREADS=1 -e VECLIB_MAXIMUM_THREADS=1

.PHONY: install lint format typecheck test train train-popularity train-cf train-itemitem train-twotower train-ranker serving-artifacts serving-artifacts-image serving-artifacts-check serve infra-up infra-down data-download data-ingest data-ingest-reset eda db-migrate db-migrate-down db-migrate-status demo-up demo-down demo-reset demo-seed demo-materialize demo-smoke demo-audits demo-load-quiesce demo-load-smoke demo-load-nightly demo-load-pages demo-load-pages-nightly demo-reliability-check demo-logs prod-env-guard up-prod prod-keycloak-provision prod-seed prod-verify prod-load prod-rollback-rehearsal prod-backup prod-edge-ca prod-logs prod-down prod-reset keycloak-export-realms web-install web-dev web-lint web-typecheck web-test web-e2e web-build api-contract api-contract-check web-api-types web-api-types-check

install:
	pip install -e ".[dev]"

lint:
	ruff check src/ synthetic/ tests/
	black --check src/ synthetic/ tests/

format:
	ruff check --fix src/ synthetic/ tests/
	black src/ synthetic/ tests/

typecheck:
	mypy src/ synthetic/

api-contract:
	python -m scripts.generate_openapi

api-contract-check:
	python -m scripts.generate_openapi --check

web-api-types:
	cd web && npm run api:types

web-api-types-check:
	cd web && npm run api:types:check

test:
	pytest tests/ -v

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

test-feature-parity:
	pytest tests/feature_parity/ -v

train-popularity:
	python -m src.training.popularity

train-cf:
	python -m src.training.cf

train-itemitem:
	python -m src.training.itemitem

train-twotower:
	python -m src.training.twotower

train-ranker:
	python -m src.training.ranker

# Non-negotiable #5's entry point: a fixed seed and a fixed as-of produce the
# same artifact hashes. It is the serving-bundle build under the name the
# non-negotiable uses.
train: serving-artifacts

serving-artifacts-image:
	docker build --platform $(ARTIFACT_PLATFORM) \
		-f infra/features/Dockerfile -t $(ARTIFACT_IMAGE) .

# Training logs go to stderr; stdout carries the bundle out of the container as
# a tar stream, which sidesteps the uid mismatch a writable bind mount would
# hit between the image's `feastuser` and whatever user CI runs as. It lands in
# a file rather than a pipe so that a failed build fails this target instead of
# handing an empty archive to a tar that shrugs.
serving-artifacts: serving-artifacts-image
	@mkdir -p $(ARTIFACT_DIR) artifacts
	$(ARTIFACT_RUN) $(ARTIFACT_IMAGE) sh -c \
		'python -m src.training.demo_artifacts --train-only \
			--as-of $(ARTIFACT_AS_OF) --output-dir /tmp/bundle >&2 \
			&& tar -c -C /tmp/bundle .' > artifacts/serving-bundle.tar
	tar -x -C $(ARTIFACT_DIR) -f artifacts/serving-bundle.tar

# Rebuilds into a scratch directory inside the container and fails if the
# committed bundle differs. The mount is read-only: a check must never be able
# to repair what it is checking.
serving-artifacts-check: serving-artifacts-image
	$(ARTIFACT_RUN) -v "$(CURDIR)/$(ARTIFACT_DIR):/app/committed:ro" $(ARTIFACT_IMAGE) \
		python -m src.training.demo_artifacts --check \
			--as-of $(ARTIFACT_AS_OF) --output-dir /app/committed

serve:
	uvicorn src.serving.app:app --host 0.0.0.0 --port 8000 --reload

web-install:
	cd web && npm install

web-dev:
	cd web && npm run dev

web-lint:
	cd web && npm run lint

web-typecheck:
	cd web && npm run typecheck

web-test:
	cd web && npm test

web-e2e:
	cd web && npm run test:e2e

web-build:
	cd web && npm run build

infra-up:
	docker compose up -d

infra-down:
	docker compose down

data-download:
	python -m src.data.download

data-ingest:
	python -m src.data.ingest

data-ingest-reset:
	python -m src.data.ingest --reset

eda:
	python -m notebooks.eda

dvc-pull:
	dvc pull

dvc-push:
	dvc push

# --- Alembic migrations -----------------------------------------------------
# The Phase 3 tenant scaffolding (public.tenants, tenant_id columns, RLS
# policies, DB roles) is applied by Alembic. Run `db-migrate` after
# `data-ingest` on a fresh dev DB; run it standalone to catch up an
# existing DB that pre-dates the Phase 3 changes.
db-migrate:
	alembic upgrade head

db-migrate-down:
	alembic downgrade -1

db-migrate-status:
	alembic current

# --- Repeatable portfolio demo ----------------------------------------------
# The explicit project name isolates demo volumes from the normal dev stack.
# `demo-reset` is therefore destructive only to movielens-demo resources.
demo-up:
	$(DEMO_COMPOSE) up -d --build --wait --wait-timeout 180 api web keycloak redis
	@if docker run --rm -v movielens-demo_model_artifacts:/artifacts \
		movielens-recsys/api:demo python -c \
		"from pathlib import Path; raise SystemExit(not Path('/artifacts/manifest.json').is_file())"; then \
		$(DEMO_COMPOSE) up -d --wait --wait-timeout 60 feature-server model-server; \
	fi
	$(DEMO_COMPOSE) run --rm demo-setup python -m synthetic.smoke.demo --readiness-only --api-url http://api:8000 --web-url http://web:3001 --keycloak-url http://keycloak:8080

demo-seed:
	$(DEMO_COMPOSE) run --rm demo-setup python -c "from synthetic.personas.seed import main; main()"
	$(MAKE) demo-materialize

demo-materialize:
	$(DEMO_COMPOSE) build feature-setup
	$(DEMO_COMPOSE) run --rm feature-setup
	$(DEMO_COMPOSE) up -d --force-recreate --wait --wait-timeout 60 feature-server model-server

demo-smoke:
	$(DEMO_COMPOSE) run --rm demo-setup python -m synthetic.smoke.demo --api-url http://api:8000 --web-url http://web:3001 --keycloak-url http://keycloak:8080

demo-audits:
	$(DEMO_COMPOSE) run --rm demo-setup python -m synthetic.smoke.demo --audits-only --api-url http://api:8000 --web-url http://web:3001 --keycloak-url http://keycloak:8080

# Stop everything the load gate does not measure. The browser demo's `api`
# and `web` processes and the one-shot setup jobs are not on the measured
# path, but on a shared CI runner they compete for the same CPU as the
# processes that are — and CPU starvation there arrives as tail latency here.
# Containers are stopped, not removed, so `demo-logs` still explains a
# failure afterwards. Run between `demo-seed` and `demo-load-smoke`.
demo-load-quiesce:
	$(DEMO_COMPOSE) stop --timeout 20 web api demo-setup feature-setup

demo-load-smoke:
	@LOAD_PROFILE=smoke K6_PUSH_INTERVAL=2m LOAD_RESULTS_DIR=$(LOAD_RESULTS_DIR) $(LOAD_GATE)

demo-load-nightly:
	@LOAD_PROFILE=nightly K6_PUSH_INTERVAL=30s LOAD_RESULTS_DIR=./artifacts/load-nightly $(LOAD_GATE)

# The page-shaped budgets: what each route's fan-out, cursor continuation,
# library read, mutation-plus-read and quick-pick sequence cost, measured
# per step. Separate from the recommendation gate on purpose — that one is
# pinned and this one is still earning its thresholds.
demo-load-pages:
	@LOAD_PROFILE=smoke K6_PUSH_INTERVAL=2m LOAD_SCRIPT=/scripts/pages.js \
		LOAD_WORKLOAD=pages LOAD_LATENCY_ENFORCED=$(PAGE_LATENCY_ENFORCED) \
		LOAD_RESULTS_DIR=$(PAGE_RESULTS_DIR) $(LOAD_GATE)

demo-load-pages-nightly:
	@LOAD_PROFILE=nightly K6_PUSH_INTERVAL=30s LOAD_SCRIPT=/scripts/pages.js \
		LOAD_WORKLOAD=pages LOAD_LATENCY_ENFORCED=true \
		LOAD_RESULTS_DIR=./artifacts/load-pages-nightly $(LOAD_GATE)

# Non-latency serving promises that a percentile cannot express: the request id
# survives to the audit row, /healthz is reachable without a token while nothing
# else is, dependency provenance is visible somewhere real, degraded metadata
# renders instead of failing, and rate limiting answers 429 with a Retry-After
# where ADR 0014 turns it on.
# Runs against the same warm stack the load gate just measured.
demo-reliability-check:
	@mkdir -p $(PAGE_RESULTS_DIR)
	$(DEMO_COMPOSE) --profile load run --rm -T demo-setup \
		python -m synthetic.load.reliability \
		--api-url http://api-load:8000 --keycloak-url http://keycloak:8080 \
		> $(PAGE_RESULTS_DIR)/reliability.json

demo-down:
	$(DEMO_COMPOSE) down --remove-orphans

demo-reset:
	$(DEMO_COMPOSE) down --volumes --remove-orphans
	$(MAKE) demo-up
	$(MAKE) demo-seed

demo-logs:
	$(DEMO_COMPOSE) logs --tail=200 api web demo-setup feature-server model-server postgres pgbouncer keycloak redis

# --- Production-mode rehearsal ----------------------------------------------
# The order below is the deployment's real order, and it is not the demo
# stack's. Keycloak has to exist before its realms, the realms have to exist
# before the API can become ready (/readyz probes the serving realm's JWKS),
# and the schema has to exist before the sidecar's feature materialization can
# fence on it. Compose expresses only the first hop of that with depends_on, so
# the rest is these targets.
#
# Nothing on this stack is destructive to `movielens-demo`: different project,
# different volumes, different image tags.

# Every secret in the stack is generated, so there is no default env file to
# fall back on. Failing here, with the two commands that fix it, beats failing
# four services later on an interpolation error.
prod-env-guard:
	@test -f $(PROD_ENV_FILE) || { \
		echo "$(PROD_ENV_FILE) is missing."; \
		echo "  cp infra/deploy/production.env.example $(PROD_ENV_FILE)"; \
		echo "  then replace every REPLACE_ME__ value with:"; \
		echo "    python -c \"import secrets; print(secrets.token_urlsafe(48))\""; \
		exit 1; }

up-prod: prod-env-guard
	$(PROD_COMPOSE_ALL) build
	$(PROD_COMPOSE) up -d --wait --wait-timeout 240 postgres-app postgres-keycloak redis edge
	$(PROD_COMPOSE) run --rm -T postgres-provision
	$(PROD_COMPOSE) up -d --wait --wait-timeout 300 pgbouncer keycloak

# Realms, clients, the audience mapper and the three named accounts. Separate
# from prod-seed so R-6 can run it twice and confirm the second run reports no
# change -- a non-idempotent provisioning script is the kind of thing that only
# shows up on the second deploy.
prod-keycloak-provision: prod-env-guard
	$(PROD_COMPOSE) run --rm -T keycloak-provision

# preflight -> schema -> seed as migrator, then the feature materialization the
# sidecar's pre-deploy performs, then the serving tier. The API is started here
# rather than in up-prod because it cannot become ready until both the realm
# and the migrations exist, and a service that can never pass its healthcheck
# is a worse signal than one that has not been asked to start yet.
prod-seed: prod-env-guard prod-keycloak-provision
	$(PROD_COMPOSE) run --rm -T release
	$(PROD_COMPOSE) run --rm -T materialize
	$(PROD_COMPOSE) up -d --wait --wait-timeout 300 feature-server model-server api web

# The post-deploy matrix, in the order the rows depend on each other: the
# in-deployment checks first (readiness, issuer equality, realm invariants,
# cold-start and learned serving, the write path, artifact provenance, the
# audit SLI), then cross-tenant isolation, then the non-latency serving
# promises. Each one exits non-zero on a finding, so `make` stops at the first.
#
# The reliability harness rides in the verify service rather than one of its
# own: it is in the same image, it wants the same identity, and the API
# image's entrypoint execs an unrecognised mode as given.
prod-verify: prod-env-guard
	$(PROD_COMPOSE) run --rm -T verify
	$(PROD_COMPOSE) run --rm -T canary
	$(PROD_COMPOSE) run --rm -T verify sh -c \
		'exec python -m synthetic.load.reliability \
			--api-url "$$API_URL" --keycloak-url "$$KEYCLOAK_URL" \
			--realm "$$VERIFY_REALM" --client-id "$$VERIFY_CLIENT_ID" \
			--client-secret "$$VERIFY_CLIENT_SECRET" \
			--username "$$VERIFY_USERNAME" --password "$$VERIFY_PASSWORD"'

# V-10. Deliberately weaker than the pinned CI gate, which needs Compose
# --force-recreate, docker stats and a cgroup probe and has no remote form:
# correctness and the warm-traffic learned assertion are enforced, p99 is
# recorded with no verdict. CI keeps the verdict.
prod-load: prod-env-guard
	$(PROD_COMPOSE) run --rm -T loadcheck

# R-12. Proves the pre-deploy schema step declines to act when the database is
# ahead of the image running it -- the difference between a rollback that ends
# an incident and one that starts a second.
prod-rollback-rehearsal: prod-env-guard
	$(PROD_COMPOSE) run --rm -T rollback-rehearsal

prod-backup: prod-env-guard
	$(PROD_COMPOSE) run --rm -T backup

# The edge's own CA root, for trusting https://app.localtest.me in a browser
# (R-5) or with curl --cacert. The containers that need it read it from the
# shared volume instead.
prod-edge-ca: prod-env-guard
	@$(PROD_COMPOSE) exec -T edge cat /edge-ca/root.crt

prod-logs: prod-env-guard
	$(PROD_COMPOSE) logs --tail=200 edge web api model-server feature-server \
		keycloak pgbouncer postgres-app postgres-keycloak redis

prod-down: prod-env-guard
	$(PROD_COMPOSE_ALL) down --remove-orphans

# Destructive to movielens-prod only. The whole point of the rehearsal is that
# the release sequence works from empty volumes with no manual priming, so this
# is the state every rehearsal run starts from.
prod-reset: prod-env-guard
	$(PROD_COMPOSE_ALL) down --volumes --remove-orphans
	$(MAKE) up-prod

# --- Keycloak realms --------------------------------------------------------
# Dumps the current live realm state (from the running Keycloak container)
# to infra/keycloak/realms/*.json so any changes made via the admin UI can
# be committed. See ADR 0007's realm-drift mitigation.
keycloak-export-realms:
	@echo "Exporting live realms to infra/keycloak/realms/ ..."
	docker compose exec -T keycloak /opt/keycloak/bin/kc.sh export \
		--dir /opt/keycloak/data/import \
		--realm default \
		--users realm_file
	docker compose exec -T keycloak /opt/keycloak/bin/kc.sh export \
		--dir /opt/keycloak/data/import \
		--realm demo \
		--users realm_file
	@echo "Done. Diff infra/keycloak/realms/ and commit any changes."
