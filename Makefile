.PHONY: install lint format typecheck test train train-popularity train-cf train-itemitem train-twotower train-ranker serve infra-up infra-down data-download data-ingest data-ingest-reset eda db-migrate db-migrate-down db-migrate-status keycloak-export-realms

install:
	pip install -e ".[dev]"

lint:
	ruff check src/ tests/
	black --check src/ tests/

format:
	ruff check --fix src/ tests/
	black src/ tests/

typecheck:
	mypy src/

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

infra-up:
	docker-compose up -d

infra-down:
	docker-compose down

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
