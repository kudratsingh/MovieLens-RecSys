DEMO_COMPOSE = docker compose -p movielens-demo -f docker-compose.yml -f docker-compose.demo.yml
K6_VERSION := $(strip $(shell cat infra/ci/k6-version))

.PHONY: install lint format typecheck test train train-popularity train-cf train-itemitem train-twotower train-ranker serve infra-up infra-down data-download data-ingest data-ingest-reset eda db-migrate db-migrate-down db-migrate-status demo-up demo-down demo-reset demo-seed demo-materialize demo-smoke demo-audits demo-load-smoke demo-load-nightly demo-logs keycloak-export-realms web-install web-dev web-lint web-typecheck web-build

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
	$(DEMO_COMPOSE) run --rm demo-setup python -c "import json, httpx; response = httpx.get('http://api:8000/users/900000101/audits?limit=3'); response.raise_for_status(); print(json.dumps(response.json(), indent=2))"

demo-load-smoke:
	K6_VERSION=$(K6_VERSION) LOAD_PROFILE=smoke K6_PROMETHEUS_RW_PUSH_INTERVAL=2m $(DEMO_COMPOSE) --profile load up -d --force-recreate --wait --wait-timeout 120 feature-server model-server api-load
	K6_VERSION=$(K6_VERSION) LOAD_PROFILE=smoke K6_PROMETHEUS_RW_PUSH_INTERVAL=2m $(DEMO_COMPOSE) --profile load up -d --wait --wait-timeout 120 prometheus
	K6_VERSION=$(K6_VERSION) LOAD_PROFILE=smoke K6_PROMETHEUS_RW_PUSH_INTERVAL=2m $(DEMO_COMPOSE) --profile load run --rm k6

demo-load-nightly:
	K6_VERSION=$(K6_VERSION) LOAD_PROFILE=nightly K6_PROMETHEUS_RW_PUSH_INTERVAL=30s $(DEMO_COMPOSE) --profile load up -d --force-recreate --wait --wait-timeout 120 feature-server model-server api-load
	K6_VERSION=$(K6_VERSION) LOAD_PROFILE=nightly K6_PROMETHEUS_RW_PUSH_INTERVAL=30s $(DEMO_COMPOSE) --profile load up -d --wait --wait-timeout 120 prometheus
	K6_VERSION=$(K6_VERSION) LOAD_PROFILE=nightly K6_PROMETHEUS_RW_PUSH_INTERVAL=30s $(DEMO_COMPOSE) --profile load run --rm k6

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
