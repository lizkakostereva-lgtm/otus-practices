#!/usr/bin/env bash
#
# Install / upgrade Apache Airflow IN the cluster, with DAGs delivered from the
# external git repository by git-sync (see airflow/values.yaml).
#
#   ./scripts/deploy_airflow.sh
#
# Override the DAG source on the fly:
#   GIT_REPO=https://gitlab.croc.ru/<you>/otus-practices.git GIT_BRANCH=hw_10 \
#     ./scripts/deploy_airflow.sh
#
set -euo pipefail

NAMESPACE="${NAMESPACE:-url-fraud}"
RELEASE="${RELEASE:-airflow}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
REPO_NAME="${REPO_NAME:-apache-airflow}"
CHART_VERSION="${CHART_VERSION:-1.16.0}"
GIT_REPO="${GIT_REPO:-}"
GIT_BRANCH="${GIT_BRANCH:-hw_10}"

log() { printf '\033[36m[airflow]\033[0m %s\n' "$*"; }

log "adding helm repo ${REPO_NAME} ..."
helm repo add "${REPO_NAME}" https://airflow.apache.org >/dev/null
helm repo update >/dev/null

# Generate a stable fernet key on first install (must persist across upgrades).
FERNET_KEY="${FERNET_KEY:-$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' 2>/dev/null || true)}"
if [[ -z "${FERNET_KEY}" ]]; then
  log "WARNING: cryptography not available; letting the chart generate a fernetKey"
  log "         (pass FERNET_KEY=... to keep it stable across upgrades)."
  FERNET_OPTS=()
else
  FERNET_OPTS=(--set "fernetKey=${FERNET_KEY}")
fi

GIT_OPTS=()
[[ -n "${GIT_REPO}" ]] && GIT_OPTS=(--set "dags.gitSync.repo=${GIT_REPO}" --set "dags.gitSync.branch=${GIT_BRANCH}")

log "installing ${RELEASE} (${REPO_NAME}/${RELEASE} ${CHART_VERSION}) into ${NAMESPACE} ..."
helm upgrade --install "${RELEASE}" "${REPO_NAME}/airflow" \
  --namespace "${NAMESPACE}" \
  --create-namespace \
  --version "${CHART_VERSION}" \
  -f airflow/values.yaml \
  "${FERNET_OPTS[@]}" \
  "${GIT_OPTS[@]}" \
  --wait --timeout 15m

# Point the DAG at the retrain image that was actually built/pushed.
if [[ -z "${RETRAIN_IMAGE:-}" ]]; then
  REGISTRY_ID="$(terraform -chdir=terraform output -raw registry_id 2>/dev/null || true)"
  [[ -n "${REGISTRY_ID}" ]] && RETRAIN_IMAGE="cr.yandex/${REGISTRY_ID}/url-fraud-retrain:latest"
fi
if [[ -n "${RETRAIN_IMAGE:-}" ]]; then
  log "setting Airflow Variable url_fraud_retrain_image=${RETRAIN_IMAGE}"
  kubectl -n "${NAMESPACE}" exec deploy/"${RELEASE}"-webserver -- \
    airflow variables set url_fraud_retrain_image "${RETRAIN_IMAGE}" || true
fi

log "done. Airflow UI (NodePort): http://<node-ip>:30090  (admin / admin — change it!)"
log "      DAG 'url_fraud_retrain' should appear within ~60s once git-sync clones ${GIT_REPO:-<repo in values.yaml>}."
