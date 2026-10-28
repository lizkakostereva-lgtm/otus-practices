#!/usr/bin/env bash
#
# Deploy the in-cluster MLflow tracking server (PostgreSQL backend + S3 artifacts).
#
# Required environment variables (or create the secret manually first):
#   POSTGRES_PASSWORD   password for the mlflow DB
#   S3_ACCESS_KEY       static access key id   (terraform output -raw mlflow_s3_access_key_id)
#   S3_SECRET_KEY       static secret key      (terraform output -raw mlflow_s3_secret_key)
#
#   ./scripts/deploy_mlflow.sh
#
set -euo pipefail

NAMESPACE="${NAMESPACE:-url-fraud}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
IMAGE_TAG="${IMAGE_TAG:-1.0.0}"

log() { printf '\033[36m[mlflow]\033[0m %s\n' "$*"; }

if [[ -n "${POSTGRES_PASSWORD:-}" && -n "${S3_ACCESS_KEY:-}" && -n "${S3_SECRET_KEY:-}" ]]; then
  log "creating/updating the mlflow-secrets secret ..."
  kubectl -n "${NAMESPACE}" create secret generic mlflow-secrets \
    --from-literal=POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
    --from-literal=AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY}" \
    --from-literal=AWS_SECRET_ACCESS_KEY="${S3_SECRET_KEY}" \
    --dry-run=client -o yaml | kubectl apply -f -
else
  if ! kubectl -n "${NAMESPACE}" get secret mlflow-secrets >/dev/null 2>&1; then
    log "ERROR: mlflow-secrets missing and POSTGRES_PASSWORD/S3_ACCESS_KEY/S3_SECRET_KEY not set."
    log "       Create it from mlflow/k8s/00-secret.example.yaml or export the three variables."
    exit 1
  fi
  log "reusing the existing mlflow-secrets secret."
fi

REGISTRY_ID="${YC_REGISTRY_ID:-$(terraform -chdir=terraform output -raw registry_id 2>/dev/null || true)}"
if [[ -z "${REGISTRY_ID}" ]]; then
  log "ERROR: cannot determine registry id (set YC_REGISTRY_ID)."
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
cp -R mlflow/k8s/. "${WORK}/"
BUCKET="$(terraform -chdir=terraform output -raw mlflow_bucket 2>/dev/null || echo mlflow-artifacts-ek)"
sed -i '' "s#cr.yandex/REGISTRY_ID#cr.yandex/${REGISTRY_ID}#g; s#mlflow:1.0.0#mlflow:${IMAGE_TAG}#g; s#mlflow-artifacts-ek#${BUCKET}#g" "${WORK}"/*.yaml 2>/dev/null \
  || sed -i "s#cr.yandex/REGISTRY_ID#cr.yandex/${REGISTRY_ID}#g; s#mlflow:1.0.0#mlflow:${IMAGE_TAG}#g; s#mlflow-artifacts-ek#${BUCKET}#g" "${WORK}"/*.yaml

log "applying mlflow manifests ..."
kubectl apply -k "${WORK}"

log "waiting for the mlflow deployment to become ready ..."
kubectl -n "${NAMESPACE}" rollout status deploy/mlflow-postgres --timeout=180s
kubectl -n "${NAMESPACE}" rollout status deploy/mlflow --timeout=300s

log "done. Tracking URI (in-cluster): http://mlflow.${NAMESPACE}.svc.cluster.local:5000"
log "      UI (NodePort):            http://<node-ip>:30500"
