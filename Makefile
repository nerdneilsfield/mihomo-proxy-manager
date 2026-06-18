.PHONY: help install test typecheck lint format check serve refresh clean all

CONFIG ?= examples/config.toml

##@ Setup

install: ## Install dependencies with uv
	uv sync --all-extras

##@ Quality

test: ## Run the full test suite
	uv run pytest -v

test-fast: ## Run tests, stop on first failure
	uv run pytest -x -q

test-cov: ## Run tests with coverage (term + html)
	uv run pytest --cov=src/mihomo_proxy_manager --cov-report=term-missing --cov-report=html

typecheck: ## Run ty type checker
	uv run ty check

lint: ## Lint with ruff (if installed)
	uv run ruff check src tests || echo "ruff not installed; skipping"

check: ## Validate example config (offline)
	uv run mpm check -c $(CONFIG)

all: test typecheck lint check ## Run tests + typecheck + lint + config check

##@ Runtime

serve: ## Start the provider service
	uv run mpm serve -c $(CONFIG)

refresh: ## Refresh a single source (usage: make refresh SOURCE=airport_a)
	uv run mpm refresh -c $(CONFIG) $(SOURCE)

##@ Maintenance

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .uv_cache data/cache logs
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

format: ## Format source with ruff (if installed)
	uv run ruff format src tests || echo "ruff not installed; skipping"

requirements: ## Export pinned requirements.txt (excluding editable installs)
	uv pip freeze | grep -v '^-e' > requirements.txt
	@echo "✓ Wrote requirements.txt ($$(wc -l < requirements.txt | tr -d ' ') packages)"

help: ## Show this help
	@awk 'BEGIN { \
		printf "\n\033[1m\033[34m%s\033[0m\n", "mihomo-proxy-manager"; \
		printf "\033[2mUsage: make \033[0m\033[36m<target>\033[0m \033[2m[VARIABLE=value]\033[0m\n\n"; \
		FS = ":.*##"; \
	} \
	/^##@/ { \
		printf "\033[1m\033[33m%s\033[0m\n", substr($$0, 5); \
	} \
	/^[a-zA-Z_-]+:.*?##/ { \
		printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2; \
	} \
	END { \
		printf "\n\033[2mVariables:\n  CONFIG=%s (config file path)\n  SOURCE=<name> (source to refresh)\033[0m\n", "$(CONFIG)" \
	}' $(MAKEFILE_LIST)

.DEFAULT_GOAL := help
