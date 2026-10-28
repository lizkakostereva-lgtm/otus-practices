#!/usr/bin/env bash
#
# Run the attack simulation: (re)create the producer+consumer Job and follow it.
#
#   ./scripts/run_attack.sh
#
# Tune the ramp first by editing kafka/k8s/20-attack.yaml (ConfigMap attack-config)
# or by overriding here:
#   make attack START_TPS=50 MAX_TPS=600 RAMP_SECONDS=180
#
set -euo pipefail

NAMESPACE="${NAMESPACE:-url-fraud}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
IMAGE_TAG="${IMAGE_TAG:-1.0.0}"
KUBECTL=(kubectl -n "${NAMESPACE}")
[[ -n "${KUBE_CONTEXT}" ]] && KUBECTL=(kubectl --context "${KUBE_CONTEXT}" -n "${NAMESPACE}")

log() { printf '\033[36m[attack]\033[0m %s\n' "$*"; }

# Optional overrides for the ConfigMap values.
if [[ -n "${START_TPS:-}" || -n "${MAX_TPS:-}" || -n "${RAMP_SECONDS:-}" || -n "${DURATION:-}" ]]; then
  log "applying attack-config overrides (START_TPS=${START_TPS:-} MAX_TPS=${MAX_TPS:-} RAMP_SECONDS=${RAMP_SECONDS:-} DURATION=${DURATION:-})"
  json=$(cat <<EOF
{"data":{"START_TPS":"${START_TPS:-20}","MAX_TPS":"${MAX_TPS:-400}","RAMP_SECONDS":"${RAMP_SECONDS:-300}","DURATION":"${DURATION:-900}"}}
EOF
)
  "${KUBECTL[@]}" patch configmap attack-config --type merge -p "${json}"
fi

REGISTRY_ID="${YC_REGISTRY_ID:-$(terraform -chdir=terraform output -raw registry_id 2>/dev/null || true)}"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
sed "s#cr.yandex/REGISTRY_ID#cr.yandex/${REGISTRY_ID}#g; s#url-fraud-attack:1.0.0#url-fraud-attack:${IMAGE_TAG}#g" \
  kafka/k8s/20-attack.yaml > "${WORK}/20-attack.yaml"

log "recreating the attack job ..."
"${KUBECTL[@]}" delete job url-fraud-attack --ignore-not-found
"${KUBECTL[@]}" apply -f "${WORK}/20-attack.yaml"

log "waiting for the attack pod to start ..."
"${KUBECTL[@]}" wait --for=condition=Ready pod -l app.kubernetes.io/name=url-fraud-attack --timeout=300s || true

POD="$("${KUBECTL[@]}" get pod -l app.kubernetes.io/name=url-fraud-attack -o jsonpath='{.items[0].metadata.name}')"
log "streaming logs from ${POD} (producer+consumer). Ctrl-C to detach (job keeps running)."
"${KUBECTL[@]}" logs -f "${POD}" -c producer &
"${KUBECTL[@]}" logs -f "${POD}" -c consumer
