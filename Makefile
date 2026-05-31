.PHONY: install lint format typecheck test train train-popularity serve infra-up infra-down data-download data-ingest data-ingest-reset eda

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
