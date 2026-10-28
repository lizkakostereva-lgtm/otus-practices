#!/usr/bin/env bash
#
# Port-forward the ClusterIP-only UIs to localhost:
#   Grafana       -> http://localhost:30300   (also exposed as NodePort 30300)
#   Prometheus    -> http://localhost:9090
#   Alertmanager  -> http://localhost:9093
#   MLflow        -> http://localhost:5000    (also exposed as NodePort 30500)
#   Airflow       -> http://localhost:8080    (also exposed as NodePort 30090)
#
#   ./scripts/port_forward.sh
#
set -euo pipefail

MON_NAMESPACE="${MON_NAMESPACE:-monitoring}"
APP_NAMESPACE="${APP_NAMESPACE:-url-fraud}"
RELEASE="${RELEASE:-kps}"

pids=()
cleanup() { for pid in "${pids[@]}"; do kill "${pid}" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

start() { # name namespace target local:remote
  kubectl -n "$2" port-forward "svc/$3" "$4" >/dev/null 2>&1 &
  pids+=("$!")
  printf '\033[36m[pf]\033[0m %s -> %s\n' "$1" "$4"
}

start "grafana"      "${MON_NAMESPACE}" "${RELEASE}-grafana"                              30300:80
start "prometheus"   "${MON_NAMESPACE}" "${RELEASE}-kube-prometheus-stack-prometheus"     9090:9090
start "alertmanager" "${MON_NAMESPACE}" "${RELEASE}-kube-prometheus-stack-alertmanager"   9093:9093
start "mlflow"       "${APP_NAMESPACE}" "mlflow"                                          5000:5000
start "airflow"      "${APP_NAMESPACE}" "airflow-webserver"                               8080:8080

echo
echo "Forwarding. Press Ctrl-C to stop."
wait
