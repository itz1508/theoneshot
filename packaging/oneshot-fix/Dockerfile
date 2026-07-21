# ─────────────────────────────────────────────────────────────────────────────
# OneShot Fix — independently runnable container
#
# Build context:  D:\Dev\Theoneshot  (the parent repository root)
# Dockerfile:     packaging/oneshot-fix/Dockerfile
#
# Frozen install: pinned dependency versions exported from each package's
#                 uv.lock via `uv export --frozen --no-hashes`.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ── Runtime dependencies (frozen from runtime/uv.lock) ──────────────────────
COPY packaging/oneshot-fix/runtime-requirements.txt /tmp/runtime-requirements.txt
COPY packaging/oneshot-fix/backend-requirements.txt /tmp/backend-requirements.txt

RUN pip install --requirement /tmp/runtime-requirements.txt \
 && pip install "jsonschema>=4.24,<5"

# ── Install the audisor runtime package (editable-free) ─────────────────────
COPY openai_project/runtime/src /opt/oneshot-fix/runtime-src/src
COPY openai_project/runtime/pyproject.toml /opt/oneshot-fix/runtime-src/pyproject.toml
RUN pip install /opt/oneshot-fix/runtime-src

# ── Install audisor_backend (the Fix engine) ────────────────────────────────
COPY audisor_backend /opt/oneshot-fix/backend-src
RUN pip install --requirement /tmp/backend-requirements.txt \
 && pip install /opt/oneshot-fix/backend-src

# ── Writable data directories ───────────────────────────────────────────────
RUN mkdir -p /data/operations /data/fix-operations /data/artifacts
ENV AUDISOR_OPERATION_DATA_DIR=/data/operations \
    AUDISOR_FIX_DATA_DIR=/data/fix-operations \
    AUDISOR_ARTIFACT_DATA_DIR=/data/artifacts

# ── Non-root user ───────────────────────────────────────────────────────────
RUN groupadd --gid 10001 oneshot \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin oneshot \
 && chown -R oneshot:oneshot /data
USER 10001:10001

WORKDIR /workspace

# ── Health / readiness check ────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD ["audisor", "aflow", "status"]

ENTRYPOINT ["audisor"]
CMD ["host", "accept"]
