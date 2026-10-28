#!/usr/bin/env bash
#
# Deploy the in-cluster single-broker Kafka used by the attack simulation.
#
#   ./scripts/deploy_kafka.sh
#
set -euo pipefail

NAMESPACE="${NAMESPACE:-url-fraud}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
IMAGE_TAG="${IMAGE_TAG:-1.0.0}"
log() { printf '\033[36m[kafka]\033[0m %s\n' "$*"; }

REGISTRY_ID="${YC_REGISTRY_ID:-$(terraform -chdir=terraform output -raw registry_id 2>/dev/null || true)}"
if [[ -z "${REGISTRY_ID}" ]]; then
  log "ERROR: cannot determine registry id (set YC_REGISTRY_ID)."
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
cp -R kafka/k8s/. "${WORK}/"
sed -i '' "s#cr.yandex/REGISTRY_ID#cr.yandex/${REGISTRY_ID}#g; s#url-fraud-attack:1.0.0#url-fraud-attack:${IMAGE_TAG}#g" "${WORK}"/*.yaml 2>/dev/null \
  || sed -i "s#cr.yandex/REGISTRY_ID#cr.yandex/${REGISTRY_ID}#g; s#url-fraud-attack:1.0.0#url-fraud-attack:${IMAGE_TAG}#g" "${WORK}"/*.yaml

log "applying kafka manifests ..."
kubectl apply -k "${WORK}"

log "waiting for the broker to become ready ..."
kubectl -n "${NAMESPACE}" rollout status deploy/kafka --timeout=300s

log "bootstrap (in-cluster): kafka.${NAMESPACE}.svc.cluster.local:9092"
