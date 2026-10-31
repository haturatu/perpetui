UV ?= uv

.PHONY: setup run demo test lint format check build

setup:
	$(UV) sync --dev

run:
	$(UV) run perpetui

demo:
	$(UV) run perpetui --demo

test:
	$(UV) run pytest

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:
	$(UV) run ruff format .

check: lint test

build:
	$(UV) build
