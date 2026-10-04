SHELL := /bin/bash

COMPOSE = docker compose
CLI = uv run house-finder

.PHONY: default up down logs check watch sync lock test coverage format lint hooks clean help

default: help

up: ##@ Build and start the watcher in Docker; it restarts with the Docker daemon
	@test -f .env || { echo 'No .env: copy .env.example to .env and fill in the token and the chat id.'; exit 1; }
	$(COMPOSE) up -d --build

down: ##@ Stop the watcher
	$(COMPOSE) down

logs: ##@ Follow the watcher logs
	$(COMPOSE) logs -f

check: ##@ Check every portal once and print the listings instead of sending them
	$(CLI) check --dry-run

watch: ##@ Watch the portals until Ctrl-C
	$(CLI) watch

sync: ##@ Install the project and development dependencies
	uv sync --locked

lock: ##@ Refresh the lockfile
	uv lock

test: ##@ Run tests
	uv run pytest

coverage: ##@ Run tests and report coverage per file
	uv run coverage run -m pytest
	uv run coverage report

format: ##@ Format Python files
	uv run ruff format .

lint: ##@ Run all pre-commit checks
	uv run pre-commit run --all-files

hooks: ##@ Install pre-commit and commit-message hooks
	uv run pre-commit install --hook-type pre-commit --hook-type commit-msg

clean: ##@ Remove generated local artifacts
	rm -rf .coverage .pytest_cache .ruff_cache

help: ##@ Show available commands
	@printf "Usage: make <command>\n\n"
	@awk 'BEGIN {FS = ":.*##@ "} /^[a-zA-Z_\/-]+:.*##@ / {printf "  %-9s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
