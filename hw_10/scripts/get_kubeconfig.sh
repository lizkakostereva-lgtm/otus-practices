#!/usr/bin/env bash
#
# Fetch the Managed Kubernetes kubeconfig into a dedicated file, register it as
# its own kubectl context, and merge it into ~/.kube/config so plain
# `kubectl --context <name>` works. Safe to re-run.
#
#   ./scripts/get_kubeconfig.sh [destination]
#
# Environment:
#   CLUSTER_NAME   Managed Kubernetes cluster name (default: url-fraud-cluster)
#   KUBE_CONTEXT   kubectl context name to create/update (default: $CLUSTER_NAME)
#   NAMESPACE      namespace set on the context          (default: url-fraud)
#   ENDPOINT_MODE  external (default) or internal.
#                  "external" works from anywhere; "internal" only from
#                  inside the VPC.
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-url-fraud-cluster}"
KUBE_CONTEXT="${KUBE_CONTEXT:-${CLUSTER_NAME}}"
NAMESPACE="${NAMESPACE:-url-fraud}"
ENDPOINT_MODE="${ENDPOINT_MODE:-external}"
DEST="${1:-${HOME}/.kube/config-${KUBE_CONTEXT}}"

# If KUBECONFIG is set, kubectl ignores ~/.kube/config, so that is where the
# context has to be merged for plain `kubectl` to see it. kubectl reads the
# entries left to right, so the first existing file is the primary one.
KUBECONFIG_FILES="${KUBECONFIG:-${HOME}/.kube/config}"
PRIMARY_KUBECONFIG="${KUBECONFIG_FILES%%:*}"
export KUBECONFIG="${KUBECONFIG:-${HOME}/.kube/config}"

command -v yc >/dev/null 2>&1 || {
  echo "error: yc CLI not found. Install: https://yandex.cloud/docs/cli/quickstart" >&2
  exit 1
}
command -v kubectl >/dev/null 2>&1 || {
  echo "error: kubectl not found. Install: https://kubernetes.io/docs/tasks/tools/" >&2
  exit 1
}

case "${ENDPOINT_MODE}" in
  external|internal) ;;
  *)
    echo "error: ENDPOINT_MODE must be 'external' or 'internal', got '${ENDPOINT_MODE}'" >&2
    exit 1
    ;;
esac

mkdir -p "$(dirname "${DEST}")"

# This CLI (>= 0.10x) calls the subcommand "get-credentials" and writes the file
# directly via --kubeconfig, naming the context via --context-name. No manual
# rename/merge of contexts is needed.
echo "==> Fetching kubeconfig for '${CLUSTER_NAME}' (${ENDPOINT_MODE} endpoint)"
yc managed-kubernetes cluster get-credentials \
  --name "${CLUSTER_NAME}" \
  --kubeconfig "${DEST}" \
  --context-name "${KUBE_CONTEXT}" \
  --force \
  --"${ENDPOINT_MODE}"
chmod 600 "${DEST}"

echo "==> Wrote ${DEST}"

# Default the context's namespace without touching any other context.
kubectl --kubeconfig "${DEST}" config set-context "${KUBE_CONTEXT}" \
  --namespace "${NAMESPACE}" >/dev/null

# Merge into the effective kubeconfig so plain `kubectl --context ...` works
# without further setup. Reads every file that actually exists, writes
# atomically, so a failure can never truncate the user's kubeconfig.
MERGE_INPUT=()
IFS=':' read -r -a _kc_files <<<"${KUBECONFIG_FILES}"
for _f in "${_kc_files[@]}"; do
  [ -f "${_f}" ] && MERGE_INPUT+=("${_f}")
done
MERGE_INPUT+=("${DEST}")

TMP="$(mktemp)"
trap 'rm -f "${TMP}"' EXIT

if KUBECONFIG="$(IFS=:; echo "${MERGE_INPUT[*]}")" \
   kubectl config view --flatten >"${TMP}" 2>/dev/null; then
  mkdir -p "$(dirname "${PRIMARY_KUBECONFIG}")"
  cp "${TMP}" "${PRIMARY_KUBECONFIG}"
  chmod 600 "${PRIMARY_KUBECONFIG}"
  echo "==> Merged into ${PRIMARY_KUBECONFIG}"
else
  echo "warning: could not merge into ${PRIMARY_KUBECONFIG}; use --kubeconfig ${DEST}" >&2
fi

# Prove the context is actually visible to a bare `kubectl` invocation.
if kubectl config get-contexts "${KUBE_CONTEXT}" >/dev/null 2>&1; then
  echo "==> Verified: context '${KUBE_CONTEXT}' is visible to plain kubectl"
else
  echo "warning: context not visible to plain kubectl. Use either" >&2
  echo "         kubectl --kubeconfig ${DEST} --context ${KUBE_CONTEXT} ..." >&2
  echo "         or export KUBECONFIG=${DEST}:${KUBECONFIG_FILES}" >&2
fi

cat <<EOF

==> Context '${KUBE_CONTEXT}' is ready (namespace: ${NAMESPACE})

    kubectl --context ${KUBE_CONTEXT} get nodes
    kubectl --context ${KUBE_CONTEXT} -n ${NAMESPACE} get all

If the nodes are not READY yet, wait ~2-5 minutes and re-run:
    kubectl --context ${KUBE_CONTEXT} get nodes -w
EOF
