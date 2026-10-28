# MLflow tracking server image: Python base + MLflow and the drivers it needs
# to talk to PostgreSQL (metadata) and S3 (artifacts).
#
# Why not ghcr.io/mlflow/mlflow: the upstream image is not reachable from every
# registry/network, while PyPI is. Building from python:slim keeps the image
# reproducible and lets us pin all three packages.
#
# Build & push (done by scripts/build_push_images.sh):
#   docker build -t cr.yandex/<registry-id>/mlflow:2.22.0 mlflow/
#   docker push  cr.yandex/<registry-id>/mlflow:2.22.0
FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir \
      mlflow==2.22.0 \
      psycopg2-binary==2.9.10 \
      boto3==1.40.0

EXPOSE 5000
