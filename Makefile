DEMO_COMPOSE = docker compose -p movielens-demo -f docker-compose.yml -f docker-compose.demo.yml
K6_VERSION := $(strip $(shell cat infra/ci/k6-version))

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

.PHONY: install lint format typecheck test train train-popularity train-cf train-itemitem train-twotower train-ranker serve infra-up infra-down data-download data-ingest data-ingest-reset eda db-migrate db-migrate-down db-migrate-status demo-up demo-down demo-reset demo-seed demo-materialize demo-smoke demo-audits demo-load-quiesce demo-load-smoke demo-load-nightly demo-load-pages demo-load-pages-nightly demo-reliability-check demo-logs keycloak-export-realms web-install web-dev web-lint web-typecheck web-test web-e2e web-build api-contract api-contract-check web-api-types web-api-types-check

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
# renders instead of failing, and rate limiting is reported honestly as absent.
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
