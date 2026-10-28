#!/usr/bin/env bash
#
# Watch the automated scaling while the attack runs: HPA targets, the current
# replica count (should climb 4 -> 6) and per-pod CPU. Optionally checks the
# fired alert through the Alertmanager API.
#
#   ./scripts/verify_scaling.sh [seconds]
#
set -euo pipefail

NAMESPACE="${NAMESPACE:-url-fraud}"
DURATION="${1:-600}"
INTERVAL=10

KUBECTL=(kubectl -n "${NAMESPACE}")

log() { printf '\033[36m[scale]\033[0m %s\n' "$*"; }

log "watching for ${DURATION}s (Ctrl-C to stop). Target: 4 -> 6 replicas, load >80% per pod."
printf '\n'

end=$(( $(date +%s) + DURATION ))
while [[ "$(date +%s)" -lt "${end}" ]]; do
  ts="$(date '+%H:%M:%S')"
  hpa="$("${KUBECTL[@]}" get hpa url-fraud-api --no-headers 2>/dev/null || echo 'hpa not found')"
  replicas="$("${KUBECTL[@]}" get deploy url-fraud-api -o jsonpath='{.status.readyReplicas}/{.status.replicas}' 2>/dev/null || echo '?')"
  printf '%s  replicas=%s  %s\n' "${ts}" "${replicas}" "${hpa}"
  sleep "${INTERVAL}"
done

printf '\n'
log "final state:"
"${KUBECTL[@]}" get hpa url-fraud-api
"${KUBECTL[@]}" get pods -l app.kubernetes.io/name=url-fraud-api -o wide

log "active alerts (if Alertmanager is reachable):"
log "  kubectl -n monitoring port-forward svc/kube-prometheus-stack-alertmanager 9093:9093"
log "  curl -s localhost:9093/api/v2/alerts | jq -r '.[] | \"\\(.labels.alertname) \\(.labels.severity) \\(.status.state)\"'"
