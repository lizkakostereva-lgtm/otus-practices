# syntax=docker/dockerfile:1
#
# Production image for the malicious-URL REST API.
#
# Design notes
#   * The trained artifact (models/model.joblib) is committed to the repo, so
#     the build needs no network access to train and stays reproducible.
#   * Dependencies are installed into a virtualenv as root, then ownership is
#     handed to an unprivileged user before anything is copied into place.
#   * uvicorn runs one worker per container; replicas come from the Deployment
#     (see k8s/20-deployment.yaml), never from --workers.

# 3.12 matches the interpreter that produced models/model.joblib, so the
# committed artifact is loaded by the exact runtime it was pickled with.
ARG PYTHON_VERSION=3.12
ARG UID=10001

# ---------------------------------------------------------------- builder ---
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY requirements.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install -r requirements.txt


# ---------------------------------------------------------------- runtime ---
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG UID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PATH="/opt/venv/bin:${PATH}" \
    MODEL_PATH=/app/models/model.joblib \
    MODEL_METADATA_PATH=/app/models/metadata.json \
    LOG_LEVEL=INFO \
    ENABLE_METRICS=true \
    SERVICE_NAME=url-fraud-api \
    ENVIRONMENT=production

RUN groupadd --gid ${UID} app \
 && useradd --uid ${UID} --gid ${UID} --create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

COPY --chown=${UID}:${UID} app/ ./app/
COPY --chown=${UID}:${UID} models/ ./models/

USER ${UID}

EXPOSE 8000

# Compose-aware healthcheck; the k8s probes do the same thing over HTTP.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]