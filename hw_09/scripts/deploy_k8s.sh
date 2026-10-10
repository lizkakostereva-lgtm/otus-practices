#!/usr/bin/env bash
#
# Render and apply the k8s manifests.
#
#   ./scripts/deploy_k8s.sh cr.yandex/<registry-id>/url-fraud-api:1.0.0
#   ./scripts/deploy_k8s.sh <image> --with-pull-secret ghcr-pull
#   ./scripts/deploy_k8s.sh <image> --replicas 3 --wait 300 --with-hpa
#
# Environment:
#   KUBE_CONTEXT  kubectl context (default: url-fraud-cluster)
#   NAMESPACE     target namespace (default: url-fraud)
#   REPLICAS      override replica count
#   ROLLBACK_TIMEOUT  rollout timeout (default: 180s)
set -euo pipefail

KUBE_CONTEXT="${KUBE_CONTEXT:-url-fraud-cluster}"
NAMESPACE="${NAMESPACE:-url-fraud}"
MANIFEST_NAMESPACE="url-fraud"   # namespace hardcoded in the manifests
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)/k8s"

IMAGE=""
PULL_SECRET=""
REPLICAS="${REPLICAS:-}"
WAIT_SECONDS="${WAIT_SECONDS:-}"
WITH_HPA="false"
ROLLBACK_TIMEOUT="${ROLLBACK_TIMEOUT:-180s}"

usage() {
  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-pull-secret) PULL_SECRET="$2"; shift 2 ;;
    --replicas)         REPLICAS="$2";  shift 2 ;;
    --wait)             WAIT_SECONDS="$2"; shift 2 ;;
    --with-hpa)         WITH_HPA="true"; shift ;;
    -h|--help)          usage; exit 0 ;;
    -*)                 echo "unknown flag: $1" >&2; usage >&2; exit 1 ;;
    *)                  IMAGE="$1"; shift ;;
  esac
done

if [[ -z "${IMAGE}" ]]; then
  usage >&2
  echo >&2
  echo "error: <image:tag> is required" >&2
  exit 1
fi

# --wait is an alias for the rollout timeout.
if [[ -n "${WAIT_SECONDS}" ]]; then
  ROLLBACK_TIMEOUT="${WAIT_SECONDS}s"
fi

kc=(kubectl --context "${KUBE_CONTEXT}")

# The cluster must be reachable. The namespace is deliberately NOT checked
# here: Terraform creates the cluster, and this script creates the namespace,
# so on a brand new cluster the namespace does not exist yet by design.
if ! "${kc[@]}" cluster-info >/dev/null 2>&1; then
  cat >&2 <<EOF
error: cannot reach cluster via context '${KUBE_CONTEXT}'.

Did you run ./scripts/get_kubeconfig.sh and 'terraform apply' first?
  ./scripts/get_kubeconfig.sh
  make tf-apply
EOF
  exit 1
fi

# Render manifests into a temp dir so NAMESPACE actually takes effect
# (kubectl -n does not override an explicit metadata.namespace).
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
if [[ "${NAMESPACE}" != "${MANIFEST_NAMESPACE}" ]]; then
  echo "==> Rendering manifests for namespace '${NAMESPACE}'"
  for f in "${K8S_DIR}"/*.yaml; do
    sed "s/^\\( *namespace: *\)${MANIFEST_NAMESPACE}$/\\1${NAMESPACE}/" "$f" > "${WORK}/$(basename "$f")"
  done
  # The namespace resource itself must carry the new name too.
  sed -i '' "s/^\\( *name: *\)${MANIFEST_NAMESPACE}$/\\1${NAMESPACE}/" "${WORK}/00-namespace.yaml" 2>/dev/null \
    || sed -i "s/^\\( *name: *\)${MANIFEST_NAMESPACE}$/\\1${NAMESPACE}/" "${WORK}/00-namespace.yaml"
  K8S_DIR="${WORK}"
else
  cp "${K8S_DIR}"/*.yaml "${WORK}/"
  K8S_DIR="${WORK}"
fi

echo "==> Deploying ${IMAGE} to ${NAMESPACE} (context: ${KUBE_CONTEXT})"

# Namespace + ConfigMap first so the Deployment can resolve its envFrom.
"${kc[@]}" apply -f "${K8S_DIR}/00-namespace.yaml"
"${kc[@]}" apply -f "${K8S_DIR}/10-configmap.yaml"

"${kc[@]}" apply -f "${K8S_DIR}/20-deployment.yaml"

# Pin the image, the replica count and (optionally) the pull secret.
"${kc[@]}" --namespace "${NAMESPACE}" \
  set image deployment/url-fraud-api "api=${IMAGE}"

if [[ -n "${REPLICAS}" ]]; then
  "${kc[@]}" --namespace "${NAMESPACE}" \
    scale deployment/url-fraud-api --replicas "${REPLICAS}"
fi

if [[ -n "${PULL_SECRET}" ]]; then
  "${kc[@]}" --namespace "${NAMESPACE}" \
    patch deployment url-fraud-api --type=merge \
    -p "{\"spec\":{\"template\":{\"spec\":{\"imagePullSecrets\":[{\"name\":\"${PULL_SECRET}\"}]}}}}"
fi

"${kc[@]}" apply -f "${K8S_DIR}/30-service-nodeport.yaml"
"${kc[@]}" apply -f "${K8S_DIR}/60-pdb.yaml"

if [[ "${WITH_HPA}" == "true" ]]; then
  if "${kc[@]}" get --raw /apis/metrics.k8s.io/v1beta1 >/dev/null 2>&1; then
    "${kc[@]}" apply -f "${K8S_DIR}/50-hpa.yaml"
  else
    echo "warning: metrics-server not available, skipping HPA" >&2
  fi
else
  echo "note: HPA not applied (pass --with-hpa, requires metrics-server)"
fi

echo "==> Waiting for the rollout (timeout ${ROLLBACK_TIMEOUT})"
if "${kc[@]}" --namespace "${NAMESPACE}" \
     rollout status deployment/url-fraud-api --timeout="${ROLLBACK_TIMEOUT}"; then
  echo "==> Rollout finished"
else
  echo "==> Rollout did NOT finish in ${ROLLBACK_TIMEOUT}. Recent events:" >&2
  "${kc[@]}" --namespace "${NAMESPACE}" \
    describe pods -l app.kubernetes.io/name=url-fraud-api | tail -40 >&2
  exit 1
fi

echo
"${kc[@]}" --namespace "${NAMESPACE}" get pods,svc -o wide

NODE_PORT="$("${kc[@]}" --namespace "${NAMESPACE}" \
  get svc url-fraud-api -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || true)"
NODE_IP="$("${kc[@]}" get nodes \
  -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}' 2>/dev/null || true)"

cat <<EOF

==> Next steps
    kubectl --context ${KUBE_CONTEXT} -n ${NAMESPACE} logs -l app.kubernetes.io/name=url-fraud-api -f
EOF

if [[ -n "${NODE_IP}" && -n "${NODE_PORT}" ]]; then
  cat <<EOF
    curl http://${NODE_IP}:${NODE_PORT}/health
    make smoke API_BASE_URL=http://${NODE_IP}:${NODE_PORT}
EOF
else
  echo "    (could not read a public node IP and nodePort automatically)"
fi
