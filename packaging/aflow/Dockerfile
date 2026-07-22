# ─────────────────────────────────────────────────────────────────────────────
# A-Flow — independently runnable container
#
# Build context:  the repository root (D:\Dev\Theoneshot)
# Dockerfile:     packaging/aflow/Dockerfile
#
# Build with:
#   docker build -f packaging/aflow/Dockerfile -t theoneshot-aflow:0.9.0 .
#
# Frozen install: pinned dependency versions exported from
#                 openai_project/aflow/uv.lock via
#                 `uv export --frozen --no-hashes --no-emit-workspace --no-annotate --no-dev`.
#
# Docker Hub manifest observed for python:3.11-slim-bookworm on 2026-07-21.
# Update the tag and digest together only after an explicit image review.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ── Runtime dependencies (frozen from openai_project/aflow/uv.lock) ─────────
COPY packaging/aflow/requirements.txt /tmp/requirements.txt
RUN pip install --requirement /tmp/requirements.txt

# ── Install the theoneshot-aflow package (provides the `aflow` CLI) ─────────
COPY openai_project/aflow/pyproject.toml /opt/aflow/pyproject.toml
COPY openai_project/aflow/README.md /opt/aflow/README.md
COPY openai_project/aflow/src /opt/aflow/src
COPY openai_project/aflow/schemas /opt/aflow/schemas
RUN pip install /opt/aflow

# ── Non-root user ───────────────────────────────────────────────────────────
RUN groupadd --gid 10001 aflow \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin aflow
USER 10001:10001

WORKDIR /workspace

ENTRYPOINT ["aflow"]
CMD ["--help"]
