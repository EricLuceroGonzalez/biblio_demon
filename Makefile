.DEFAULT_GOAL := help
.PHONY: help setup run dry-run test lint format check diagrams clean

help:  ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup:  ## Crea el entorno e instala dependencias (uv)
	uv sync
	uv run pre-commit install || true

run:  ## Arranca el demonio (lee .env)
	uv run biblio-demon run

dry-run:  ## Demonio en modo simulación (no mueve ni sincroniza)
	uv run biblio-demon run --dry-run

test:  ## Ejecuta los tests con cobertura
	uv run pytest

lint:  ## Revisa estilo e imports (sin modificar)
	uv run ruff check .
	uv run ruff format --check .

format:  ## Formatea y ordena imports
	uv run ruff check --fix .
	uv run ruff format .

check: lint test  ## lint + tests (lo que corre en CI)

diagrams:  ## Diagramas UML con pyreverse (requiere graphviz)
	mkdir -p docs/diagrams
	uv run pyreverse -o png -p biblio_demon -d docs/diagrams src/biblio_demon

clean:  ## Borra cachés y artefactos
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +
	rm -rf .coverage htmlcov dist build
