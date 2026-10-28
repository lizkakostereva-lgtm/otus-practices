# syntax=docker/dockerfile:1
#
# Image for the Kafka attack simulator: contains both the ramping producer and
# the inference consumer (kafka/app/*.py).
#
#   docker build -t cr.yandex/<registry-id>/url-fraud-attack:1.0.0 kafka/
FROM python:3.12-slim

ARG UID=10001
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN groupadd --gid ${UID} app \
 && useradd --uid ${UID} --gid ${UID} --create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=${UID}:${UID} app/ ./app/

USER ${UID}
