ARG STAGE="dev"
ARG DEBIAN_CODENAME="trixie"
ARG BUILD_BASE="debian:${DEBIAN_CODENAME}"
ARG RUNTIME_BASE="debian:${DEBIAN_CODENAME}-slim"
ARG APP_HOME="/opt/project"
ARG VIRTUAL_ENV="/opt/.venv"
ARG PYTHON_INSTALL_PATH="/usr/local/bin/uv-python"

ARG UV_VERSION="0.11.19"
ARG UV_BASE="ghcr.io/astral-sh/uv:${UV_VERSION}"
FROM ${UV_BASE} AS uv-image

FROM ${BUILD_BASE} AS dev-builder
ENV UV_ARGS=""

FROM ${BUILD_BASE} AS pro-builder
ENV UV_ARGS="--no-dev --no-editable"

FROM ${STAGE}-builder AS builder

ARG APP_HOME
ARG VIRTUAL_ENV
ARG PYTHON_INSTALL_PATH
ARG GROUP_DEPENDENCIES
ARG EXTRA_DEPENDENCIES

ENV DEBIAN_FRONTEND=noninteractive \
    UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=${PYTHON_INSTALL_PATH} \
    UV_PROJECT_ENVIRONMENT=${VIRTUAL_ENV} \
    PATH=/root/.local/bin:${VIRTUAL_ENV}/bin:${PATH}

WORKDIR ${APP_HOME}

# hadolint ignore=SC2086
RUN --mount=from=uv-image,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=.python-version,target=.python-version \
    --mount=type=bind,source=README.md,target=README.md \
    --mount=type=bind,source=src,target=src \
    uv venv ${VIRTUAL_ENV} && \
    uv sync --locked ${UV_ARGS} ${GROUP_DEPENDENCIES} ${EXTRA_DEPENDENCIES}

FROM ${RUNTIME_BASE} AS runtime-base

ARG APP_HOME
ARG VIRTUAL_ENV
ARG PYTHON_INSTALL_PATH

# The scrapers and Telegram need HTTPS roots, which slim images leave out.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

COPY --from=builder --chmod=755 ${PYTHON_INSTALL_PATH} ${PYTHON_INSTALL_PATH}
COPY --from=builder --chmod=755 ${VIRTUAL_ENV} ${VIRTUAL_ENV}
COPY --chmod=755 .bin/runtime_host_user_entrypoint.sh ${APP_HOME}/.bin/runtime_host_user_entrypoint.sh
COPY config/settings.toml ${APP_HOME}/config/settings.toml

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=${VIRTUAL_ENV}/bin:${PATH}

WORKDIR ${APP_HOME}

FROM runtime-base AS dev-runtime
FROM runtime-base AS pro-runtime
FROM ${STAGE}-runtime AS runtime

ENTRYPOINT ["house-finder"]
