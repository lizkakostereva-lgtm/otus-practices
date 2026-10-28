#!/usr/bin/env bash
#
# Install / upgrade the monitoring stack in the cluster:
#   * kube-prometheus-stack (Prometheus, Alertmanager, Grafana, node-exporter,
#     kube-state-metrics)
#   * the url-fraud PrometheusRule + AlertmanagerConfig (applied via manifests)
#   * the Grafana dashboards, as ConfigMaps picked up by the grafana sidecar
#
#   ./scripts/deploy_monitoring.sh
#
set -euo pipefail

NAMESPACE="${NAMESPACE:-url-fraud}"
MON_NAMESPACE="${MON_NAMESPACE:-monitoring}"
RELEASE="${RELEASE:-kube-prometheus-stack}"
CHART="${CHART:-kube-prometheus-stack}"
REPO_NAME="${REPO_NAME:-prometheus-community}"
CHART_VERSION="${CHART_VERSION:-65.1.1}"

log() { printf '\033[36m[monitoring]\033[0m %s\n' "$*"; }

# Ensure namespaces
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create namespace "${MON_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# metrics-server (official chart) — required for the HPA
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install metrics-server metrics-server/metrics-server \
  --namespace kube-system \
  --set args[0]=--kubelet-insecure-tls \
  --wait --timeout 5m || true
kubectl -n kube-system rollout status deploy/metrics-server --timeout=300s || true

log "adding helm repo ${REPO_NAME}"
helm repo add "${REPO_NAME}" https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null

log "installing ${RELEASE} (${CHART} ${CHART_VERSION}) into ${MON_NAMESPACE} ..."
helm upgrade --install "${RELEASE}" "${REPO_NAME}/${CHART}" \
  --namespace "${MON_NAMESPACE}" \
  --create-namespace \
  --version "${CHART_VERSION}" \
  -f "${ROOT_DIR}/monitoring/kube-prometheus-stack-values.yaml" \
  --wait --timeout 15m

log "ensuring the alertmanager SMTP secret exists ..."
if ! kubectl -n "${NAMESPACE}" get secret alertmanager-smtp >/dev/null 2>&1; then
  log "creating a PLACEHOLDER alertmanager-smtp secret — replace it with real SMTP creds:"
  log "  kubectl -n ${NAMESPACE} create secret generic alertmanager-smtp \\"
  log "    --from-literal=password='<smtp-password>' --dry-run=client -o yaml | kubectl apply -f -"
  kubectl -n "${NAMESPACE}" create secret generic alertmanager-smtp \
    --from-literal=password='REPLACE_ME' --dry-run=client -o yaml | kubectl apply -f -
fi

log "applying url-fraud ServiceMonitor + PrometheusRule + AlertmanagerConfig ..."
kubectl apply -n "${NAMESPACE}" -f k8s/70-servicemonitor.yaml
kubectl apply -n "${NAMESPACE}" -f monitoring/alerts/url-fraud-alerts.yaml
kubectl apply -n "${NAMESPACE}" -f monitoring/alertmanager/alertmanager-config.yaml

log "loading Grafana dashboards as configmaps ..."
for dashboard in monitoring/grafana/dashboards/*.json; do
  name="grafana-dashboard-$(basename "${dashboard}" .json | tr '_' '-')"
  kubectl -n "${MON_NAMESPACE}" create configmap "${name}" \
    --from-file="$(basename "${dashboard}")=${dashboard}" \
    --dry-run=client -o yaml \
    | kubectl label --local -f - grafana_dashboard=1 -o yaml \
    | kubectl apply -f -
done

log "registered monitoring objects:"
kubectl -n "${NAMESPACE}" get servicemonitor url-fraud-api -o name
kubectl -n "${NAMESPACE}" get prometheusrule url-fraud-alerts -o name

log "done. Grafana: http://<node-ip>:30300   (admin / url-fraud-grafana)"
log "      Prometheus/Alertmanager are ClusterIP-only; use scripts/port_forward.sh."
