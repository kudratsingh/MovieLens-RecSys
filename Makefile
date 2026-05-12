.PHONY: install lint format typecheck test train serve infra-up infra-down

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

train:
	python -m src.training.run

serve:
	uvicorn src.serving.app:app --host 0.0.0.0 --port 8000 --reload

infra-up:
	docker-compose up -d

infra-down:
	docker-compose down

dvc-pull:
	dvc pull

dvc-push:
	dvc push
