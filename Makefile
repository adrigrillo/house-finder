SHELL := /bin/bash

PROJECT_NAME ?= $(shell awk '/^\[project\]/ {found=1; next} /^\[/ {found=0} found && /^name = / {gsub(/"/, "", $$3); print $$3}' pyproject.toml)
IMAGE ?= $(PROJECT_NAME)
CACHE_DIR ?= .cache
CACHE_PATHS ?= uv,pre-commit

GROUP_DEPS_DEV := $(shell sed -n 's/^GROUP_DEPS_DEV="\(.*\)"/\1/p' config/dependencies.env | sed 's/[^ ]\+/--group &/g')
EXTRA_DEPS_DEV := $(shell sed -n 's/^EXTRA_DEPS_DEV="\(.*\)"/\1/p' config/dependencies.env | sed 's/[^ ]\+/--extra &/g')
GROUP_DEPS_PRO := $(shell sed -n 's/^GROUP_DEPS_PRO="\(.*\)"/\1/p' config/dependencies.env | sed 's/[^ ]\+/--group &/g')
EXTRA_DEPS_PRO := $(shell sed -n 's/^EXTRA_DEPS_PRO="\(.*\)"/\1/p' config/dependencies.env | sed 's/[^ ]\+/--extra &/g')

HOST_USER ?= $(shell whoami)
U_ID ?= $(shell id -u)
G_ID ?= $(shell id -g)
IS_ROOTLESS ?= $(shell docker info -f "{{println .SecurityOptions}}" 2>/dev/null | grep -q rootless && echo 1 || echo 0)
DATA_MOUNT = $(if $(strip $(DATA_PATH)),-v $(abspath $(DATA_PATH)):/data,)

.PHONY: default sync lock test coverage format lint hooks build clean dev/build dev/shell dev/run pro/build pro/shell pro/run help

default: help

sync: ##@ Install the project and development dependencies
	uv sync --locked

lock: ##@ Refresh the lockfile
	uv lock

test: ##@ Run tests
	uv run pytest

coverage: ##@ Run tests and write terminal and Sonar coverage reports
	uv run coverage run -m pytest
	uv run coverage report
	uv run coverage xml

format: ##@ Format Python files
	uv run ruff format .

lint: ##@ Run all pre-commit checks
	uv run pre-commit run --all-files

hooks: ##@ Install pre-commit and commit-message hooks
	uv run pre-commit install --hook-type pre-commit --hook-type commit-msg

build: ##@ Build the wheel and source distribution
	uv build

dev/build: ##@ Build the development image
	docker build \
		--build-arg STAGE=dev \
		--build-arg GROUP_DEPENDENCIES="$(GROUP_DEPS_DEV)" \
		--build-arg EXTRA_DEPENDENCIES="$(EXTRA_DEPS_DEV)" \
		-t $(IMAGE):dev \
		-f devops/Dockerfile .

pro/build: ##@ Build the production image
	docker build \
		--build-arg STAGE=pro \
		--build-arg GROUP_DEPENDENCIES="$(GROUP_DEPS_PRO)" \
		--build-arg EXTRA_DEPENDENCIES="$(EXTRA_DEPS_PRO)" \
		-t $(IMAGE):pro \
		-f devops/Dockerfile .

dev/shell: dev/build ##@ Open a development shell with the source mounted
	docker run --rm -it \
		-e HOST_USER=$(HOST_USER) -e U_ID=$(U_ID) -e G_ID=$(G_ID) -e IS_ROOTLESS=$(IS_ROOTLESS) \
		-e HOME=/home/$(HOST_USER) \
		--entrypoint .bin/runtime_host_user_entrypoint.sh \
		$(shell ./.bin/gen_cache_dir_mounts.sh -u $(HOST_USER) -b $(CACHE_DIR) -d "$(CACHE_PATHS)" -c) \
		-v $(CURDIR):/opt/project $(DATA_MOUNT) \
		$(IMAGE):dev bash

dev/run: dev/build ##@ Run the CLI in the development image; pass ARGS="..."
	docker run --rm \
		-e HOST_USER=$(HOST_USER) -e U_ID=$(U_ID) -e G_ID=$(G_ID) -e IS_ROOTLESS=$(IS_ROOTLESS) \
		-e HOME=/home/$(HOST_USER) \
		--entrypoint .bin/runtime_host_user_entrypoint.sh \
		$(shell ./.bin/gen_cache_dir_mounts.sh -u $(HOST_USER) -b $(CACHE_DIR) -d "$(CACHE_PATHS)" -c) \
		-v $(CURDIR):/opt/project $(DATA_MOUNT) \
		$(IMAGE):dev house-finder $(ARGS)

pro/shell: pro/build ##@ Open a shell in the production image
	docker run --rm -it --entrypoint bash $(DATA_MOUNT) $(IMAGE):pro

pro/run: pro/build ##@ Run the CLI in the production image; pass ARGS="..."
	docker run --rm $(DATA_MOUNT) $(IMAGE):pro $(ARGS)

clean: ##@ Remove generated local artifacts
	rm -rf .coverage .pytest_cache .ruff_cache build dist htmlcov reports coverage.xml

help: ##@ Show available commands
	@printf "Usage: make <command>\n\n"
	@awk 'BEGIN {FS = ":.*##@ "} /^[a-zA-Z_\/-]+:.*##@ / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
