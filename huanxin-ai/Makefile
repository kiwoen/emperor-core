.PHONY: help install install-dev test lint format coverage clean build docker-build

PYTHON := python
PIP := pip

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install core dependencies
	$(PIP) install -e .

install-dev: ## Install with dev dependencies
	$(PIP) install -e ".[dev]"

test: ## Run pytest (skip network tests)
	$(PYTHON) -m pytest tests/ -q --tb=short -m "not network" --timeout 120 \ \
		--ignore=tests/test_e2e_integration.py

test-all: ## Run full test suite including network
	$(PYTHON) -m pytest tests/ -q --tb=short --timeout 120 \ \
		--ignore=tests/test_e2e_integration.py

lint: ## Lint with ruff
	ruff check .

format: ## Format with ruff
	ruff format .
	ruff check --fix .

typecheck: ## Type check with mypy
	mypy huanxin/ --ignore-missing-imports

coverage: ## Run tests with coverage report
	$(PYTHON) -m pytest tests/ -q --tb=short -m "not network" \
		--cov=huanxin --cov-report=html --cov-report=term \ \
		--ignore=tests/test_e2e_integration.py

clean: ## Remove build artifacts and cache
	@rm -rf build/ dist/ *.egg-info .pytest_cache/ .mypy_cache/ .ruff_cache/
	@rm -rf htmlcov/ coverage.xml .coverage
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete 2>/dev/null || true

build: ## Build sdist and wheel
	$(PYTHON) -m build --sdist --wheel

docker-build: ## Build Docker image
	docker build -t huanxin-ai:latest .
