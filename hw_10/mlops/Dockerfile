# syntax=docker/dockerfile:1
#
# Image for the periodic retraining job (mlops/retrain.py).
#
# It deliberately bundles the SAME `app/` package the API image ships, so the
# model it trains is produced by exactly the code path the service loads.

ARG PYTHON_VERSION=3.12
ARG UID=10001

FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements.txt ./
COPY mlops/requirements.txt ./mlops/requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install -r mlops/requirements.txt


FROM python:${PYTHON_VERSION}-slim AS runtime

ARG UID=10001
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/app

RUN groupadd --gid ${UID} app \
 && useradd --uid ${UID} --gid ${UID} --create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=${UID}:${UID} app/ ./app/
COPY --chown=${UID}:${UID} mlops/ ./mlops/
RUN mkdir -p /tmp && chown ${UID}:${UID} /tmp

USER ${UID}

ENTRYPOINT ["python", "-m", "mlops.retrain"]
