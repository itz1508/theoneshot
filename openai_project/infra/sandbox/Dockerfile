# Build explicitly with: docker build -t audisor-sandbox:phase2b-v1 infra/sandbox
# Docker Hub manifest observed for python:3.11.11-slim-bookworm on 2026-07-16.
# Update the tag and digest together only after an explicit image review.
FROM python:3.11.11-slim-bookworm@sha256:f51b77754c50465d0974ec4a282c29e38797e6a860decc7540d57bfc47b4c36d

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Validation tooling is installed at image-build time. Runtime containers have no network.
RUN pip install --no-cache-dir pytest==8.4.2

USER 65534:65534
WORKDIR /workspace
