.PHONY: install test lint format clean smoke

PY ?= python3.12

install:
	$(PY) -m pip install -e '.[dev]'

test:
	pytest

lint:
	ruff check src tests

format:
	ruff format src tests

smoke:
	nih-agent awards search "PFAS proteomics" --years 2022:2023 --limit 3

clean:
	rm -rf data/cache
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
