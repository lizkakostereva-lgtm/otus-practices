#!/usr/bin/env bash
#
# End-to-end smoke test against a running instance.
#
#   API_BASE_URL=http://<node-ip>:30080 ./scripts/smoke_test.sh
#   make smoke API_BASE_URL=http://127.0.0.1:8000     # local uvicorn
#   make smoke-local                                  # local container
#
# Exit code 0 means every check passed.
set -uo pipefail

BASE_URL="${API_BASE_URL:-${1:-}}"
BASE_URL="${BASE_URL%/}"

if [[ -z "${BASE_URL}" ]]; then
  cat >&2 <<'EOF'
error: API_BASE_URL is not set.

Examples:
  API_BASE_URL=http://127.0.0.1:8000            ./scripts/smoke_test.sh
  API_BASE_URL=http://<node-public-ip>:30080    ./scripts/smoke_test.sh
EOF
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "error: curl is required" >&2
  exit 1
fi

PASS=0
FAIL=0

green() { printf '\033[32m%s\033[0m\n' "$1"; }
red()   { printf '\033[31m%s\033[0m\n' "$1"; }

# check <name> <expected-substring> <actual>
check() {
  local name="$1" expected="$2" actual="$3"
  if [[ "${actual}" == *"${expected}"* ]]; then
    green "  PASS  ${name}"
    PASS=$((PASS + 1))
  else
    red   "  FAIL  ${name}"
    red   "        expected to contain: ${expected}"
    red   "        got: ${actual:0:400}"
    FAIL=$((FAIL + 1))
  fi
}

# status <name> <expected-code> <actual-code>
status() {
  local name="$1" expected="$2" actual="$3"
  if [[ "${actual}" == "${expected}" ]]; then
    green "  PASS  ${name} (HTTP ${actual})"
    PASS=$((PASS + 1))
  else
    red   "  FAIL  ${name}: expected HTTP ${expected}, got ${actual}"
    FAIL=$((FAIL + 1))
  fi
}

get() {
  curl -sS --max-time "${API_TIMEOUT:-30}" -o /tmp/smoke.body -w '%{http_code}' "$1" 2>/dev/null || echo "000"
}

post() {
  curl -sS --max-time "${API_TIMEOUT:-30}" -X POST \
    -H 'Content-Type: application/json' \
    -d "$2" \
    -o /tmp/smoke.body -w '%{http_code}' "$1" 2>/dev/null || echo "000"
}

jq_get() {
  if command -v jq >/dev/null 2>&1; then
    jq -r "$1" /tmp/smoke.body 2>/dev/null
  else
    # Minimal fallback: pull the first "key": "value" or number match.
    python3 -c "
import json, re, sys
data = json.load(open('/tmp/smoke.body'))
expr = sys.argv[1].lstrip('.').replace('\"','')
for part in expr.split('.'):
    if part:
        data = data[part] if not part.isdigit() else data[int(part)]
print(data)
" "$1" 2>/dev/null
  fi
}

echo "==> Smoke-testing ${BASE_URL}"
echo

echo "[1/8] Liveness and readiness"
code="$(get "${BASE_URL}/health")"
check "GET /health -> 200"           "" "${code}"
check "health reports model_loaded"  '"model_loaded":true' "$(cat /tmp/smoke.body)"
status "GET /healthz"                "200" "$(get "${BASE_URL}/healthz")"
status "GET /readyz"                 "200" "$(get "${BASE_URL}/readyz")"

echo
echo "[2/8] Model metadata"
code="$(get "${BASE_URL}/api/v1/model/info")"
check "GET /api/v1/model/info -> 200" "" "${code}"
check "exposes model_version"        '"model_version"' "$(cat /tmp/smoke.body)"
check "exposes metrics"              '"auc"'          "$(cat /tmp/smoke.body)"

echo
echo "[3/8] Single prediction (malicious URL)"
# Real row from the training set: P(bad) = 0.89
code="$(post "${BASE_URL}/api/v1/predict" '{"url":"upstreams.info/wp-admin/includes/inst.exe"}')"
check "POST /api/v1/predict -> 200"   "" "${code}"
check "classified as fraud"           '"is_fraud":true'  "$(cat /tmp/smoke.body)"
check "label is bad"                  '"label":"bad"'     "$(cat /tmp/smoke.body)"

echo
echo "[4/8] Single prediction (legitimate URL)"
# Real row from the training set: P(bad) = 0.25
code="$(post "${BASE_URL}/api/v1/predict" '{"url":"33-montreal.com/history-of-montreal.asp"}')"
check "POST /api/v1/predict -> 200"   "" "${code}"
check "classified as legitimate"      '"is_fraud":false' "$(cat /tmp/smoke.body)"
check "label is good"                 '"label":"good"'   "$(cat /tmp/smoke.body)"

echo
echo "[5/8] Bare hostname is normalised"
code="$(post "${BASE_URL}/api/v1/predict" '{"url":"docs.python.org"}')"
check "POST /api/v1/predict -> 200"   "" "${code}"
check "scheme added"                  'http://docs.python.org' "$(cat /tmp/smoke.body)"

echo
echo "[6/8] Batch prediction"
code="$(post "${BASE_URL}/api/v1/predict/batch" \
  '{"urls":["upstreams.info/wp-admin/includes/inst.exe","github.com/faizann24"]}')"
check "POST /api/v1/predict/batch -> 200" "" "${code}"
check "count is 2"                      '"count":2'   "$(cat /tmp/smoke.body)"

echo
echo "[7/8] Input validation"
status "empty body -> 422"  "422" "$(post "${BASE_URL}/api/v1/predict" '{}')"
status "bad scheme -> 422"  "422" "$(post "${BASE_URL}/api/v1/predict" '{"url":"ftp://x.io"}')"
status "threshold > 1 -> 422" "422" "$(post "${BASE_URL}/api/v1/predict" '{"url":"x.io","threshold":1.5}')"

echo
echo "[8/8] Observability"
check "GET /docs -> 200"          "" "$(get "${BASE_URL}/docs")"
code="$(get "${BASE_URL}/openapi.json")"
check "GET /openapi.json -> 200"  "" "${code}"
code="$(get "${BASE_URL}/metrics")"
check "GET /metrics -> 200"       "" "${code}"
check "exposes counter"           'url_fraud_predictions_total' "$(cat /tmp/smoke.body)"

echo
if [[ ${FAIL} -eq 0 ]]; then
  green "==> ALL ${PASS} CHECKS PASSED against ${BASE_URL}"
  exit 0
fi
red "==> ${FAIL} FAILED, ${PASS} passed"
exit 1