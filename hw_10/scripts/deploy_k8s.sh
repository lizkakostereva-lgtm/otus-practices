#!/usr/bin/env bash
#
# Render and apply the API manifests (Deployment, Service, HPA, PDB,
# ServiceMonitor) to the cluster.
#
#   ./scripts/deploy_k8s.sh cr.yandex/<registry-id>/url-fraud-api:1.0.0
#   ./scripts/deploy_k8s.sh --registry <registry-id> --tag <tag>
#
# Environment:
#   KUBE_CONTEXT   kubectl context (default: url-fraud-cluster)
#   NAMESPACE      target namespace (default: url-fraud)
#   REPLICAS       override replica count (default: keep manifest = 4)
#   WAIT_SECONDS   rollout timeout (default: 180s)
#
# The other components have their own deploy scripts:
#   scripts/deploy_monitoring.sh  scripts/deploy_mlflow.sh
#   scripts/deploy_airflow.sh     scripts/deploy_kafka.sh
set -euo pipefail

KUBE_CONTEXT="${KUBE_CONTEXT:-url-fraud-cluster}"
NAMESPACE="${NAMESPACE:-url-fraud}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
K8S_DIR="${ROOT_DIR}/k8s"

IMAGE=""
REGISTRY_ID=""
TAG="1.0.0"
REPLICAS="${REPLICAS:-}"
WAIT_SECONDS="${WAIT_SECONDS:-180}"

usage() {
  sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --registry) REGISTRY_ID="$2"; shift 2 ;;
    --tag)      TAG="$2"; shift 2 ;;
    --replicas) REPLICAS="$2"; shift 2 ;;
    --wait)     WAIT_SECONDS="$2"; shift 2 ;;
    -h|--help)  usage; exit 0 ;;
    -*)         echo "unknown flag: $1" >&2; usage >&2; exit 1 ;;
    *)          IMAGE="$1"; shift ;;
  esac
done

if [[ -z "${IMAGE}" && -z "${REGISTRY_ID}" ]]; then
  usage >&2
  echo >&2
  echo "error: pass <image:tag> or --registry <registry-id> [--tag <tag>]" >&2
  exit 1
fi

kc=(kubectl --context "${KUBE_CONTEXT}")
if ! "${kc[@]}" cluster-info >/dev/null 2>&1; then
  cat >&2 <<EOF
error: cannot reach cluster via context '${KUBE_CONTEXT}'.
  ./scripts/get_kubeconfig.sh && make tf-apply
EOF
  exit 1
fi

# Render into a temp dir so the registry id / tag placeholders are substituted
# without touching the tracked manifests.
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
cp "${K8S_DIR}"/*.yaml "${WORK}/"

# Substitute the registry id and image tag everywhere in the rendered copy.
if [[ -n "${REGISTRY_ID}" ]]; then
  IMAGE="cr.yandex/${REGISTRY_ID}/url-fraud-api:${TAG}"
fi
sed -i '' -e "s#cr.yandex/REGISTRY_ID/url-fraud-api:1.0.0#${IMAGE}#g" "${WORK}"/*.yaml 2>/dev/null \
  || sed -i -e "s#cr.yandex/REGISTRY_ID/url-fraud-api:1.0.0#${IMAGE}#g" "${WORK}"/*.yaml

if [[ "${NAMESPACE}" != "url-fraud" ]]; then
  for f in "${WORK}"/*.yaml; do
    sed -i '' "s/namespace: url-fraud/namespace: ${NAMESPACE}/g" "$f" 2>/dev/null \
      || sed -i "s/namespace: url-fraud/namespace: ${NAMESPACE}/g" "$f"
  done
  # The Namespace object's own name must change too.
  sed -i '' "s/name: url-fraud\$/name: ${NAMESPACE}/" "${WORK}/00-namespace.yaml" 2>/dev/null \
    || sed -i "s/name: url-fraud\$/name: ${NAMESPACE}/" "${WORK}/00-namespace.yaml"
fi

echo "==> Deploying ${IMAGE} to ${NAMESPACE} (context: ${KUBE_CONTEXT})"
"${kc[@]}" apply -f "${WORK}/00-namespace.yaml"
# apply without ServiceMonitor first (CRD may not exist yet)
grep -v "70-servicemonitor.yaml" "${WORK}/kustomization.yaml" > "${WORK}/kustomization.yaml.tmp" && mv "${WORK}/kustomization.yaml.tmp" "${WORK}/kustomization.yaml"
sed -i.bak '/70-servicemonitor.yaml/d' "${WORK}/kustomization.yaml" 2>/dev/null || true
rm -f "${WORK}/kustomization.yaml.bak" 2>/dev/null || true
"${kc[@]}" apply -k "${WORK}"
# apply ServiceMonitor if CRD exists
if "${kc[@]}" get crd servicemonitors.monitoring.coreos.com >/dev/null 2>&1; then
  "${kc[@]}" apply -f "${WORK}/70-servicemonitor.yaml" || true
fi

if [[ -n "${REPLICAS}" ]]; then
  "${kc[@]}" -n "${NAMESPACE}" scale deployment/url-fraud-api --replicas "${REPLICAS}"
fi

echo "==> Waiting for the rollout (timeout ${WAIT_SECONDS}s)"
if ! "${kc[@]}" -n "${NAMESPACE}" rollout status deployment/url-fraud-api --timeout="${WAIT_SECONDS}s"; then
  echo "==> Rollout did NOT finish. Recent events:" >&2
  "${kc[@]}" -n "${NAMESPACE}" describe pods -l app.kubernetes.io/name=url-fraud-api | tail -40 >&2
  exit 1
fi

echo
"${kc[@]}" -n "${NAMESPACE}" get deploy,svc,hpa,servicemonitor -o wide

NODE_IP="$("${kc[@]}" get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}' 2>/dev/null || true)"
cat <<EOF

==> Next steps
    make deploy-monitoring    # Prometheus + Grafana + alerts
    make deploy-mlflow        # MLflow
    make deploy-airflow       # Airflow (git-synced DAGs)
    make deploy-kafka         # in-cluster Kafka
    make attack               # run the attack simulation
    make smoke API_BASE_URL=http://${NODE_IP:-<node-ip>}:30080
EOF
