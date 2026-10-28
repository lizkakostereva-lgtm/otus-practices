#!/usr/bin/env bash
#
# Build and push every hw_10 image to Yandex Container Registry.
#
#   YC_REGISTRY_ID=<id> ./scripts/build_push_images.sh [tag]
#
# Images:
#   url-fraud-api       app/            FastAPI model service
#   url-fraud-retrain   mlops/          Airflow retraining job (Dockerfile in mlops/)
#   url-fraud-attack    kafka/          Kafka producer + consumer (attack)
#   mlflow              mlflow/         MLflow tracking server
#
set -euo pipefail

TAG="${1:-$(git rev-parse --short HEAD 2>/dev/null || echo 1.0.0)}"
REGISTRY="${YC_REGISTRY:-cr.yandex}"
REGISTRY_ID="${YC_REGISTRY_ID:-$(terraform -chdir=terraform output -raw registry_id 2>/dev/null || true)}"
PLATFORM="${PLATFORM:-linux/amd64}"

if [[ -z "${REGISTRY_ID}" ]]; then
  echo "ERROR: YC_REGISTRY_ID is empty. Run 'make tf-apply' or export YC_REGISTRY_ID." >&2
  exit 1
fi

BASE="${REGISTRY}/${REGISTRY_ID}"

log() { printf '\033[36m[builder]\033[0m %s\n' "$*"; }

log "logging into ${REGISTRY} ..."
yc iam create-token | docker login "${REGISTRY}" -u iam --password-stdin >/dev/null

build_push() {
  local name="$1" context="$2" dockerfile="$3"
  local ref="${BASE}/${name}:${TAG}"
  log "building ${ref} (context=${context}, dockerfile=${dockerfile})"
  docker build --platform "${PLATFORM}" -f "${dockerfile}" -t "${ref}" "${context}"
  docker push "${ref}"
  docker tag "${ref}" "${BASE}/${name}:latest"
  docker push "${BASE}/${name}:latest"
  log "pushed ${ref} and :latest"
}

build_push "url-fraud-api"     "."       "Dockerfile"
build_push "url-fraud-retrain" "."       "mlops/Dockerfile"
build_push "url-fraud-attack"  "kafka"   "kafka/Dockerfile"
build_push "mlflow"            "mlflow"  "mlflow/Dockerfile"

cat <<EOF

Done. Use these references in the manifests (or pass to make):
  API image     : ${BASE}/url-fraud-api:${TAG}
  Retrain image : ${BASE}/url-fraud-retrain:${TAG}
  Attack image  : ${BASE}/url-fraud-attack:${TAG}
  MLflow image  : ${BASE}/mlflow:${TAG}

Next:
  make deploy            # API + ServiceMonitor + HPA
  make deploy-monitoring # kube-prometheus-stack + dashboards
EOF
