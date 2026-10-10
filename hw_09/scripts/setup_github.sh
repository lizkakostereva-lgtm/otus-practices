#!/usr/bin/env bash
#
# One-time GitHub configuration for the hw_09 pipeline.
#
# Sets the repository variables and the single secret that the `push` and
# `deploy` jobs need, through the gh CLI instead of by hand in
# Settings -> Secrets and variables -> Actions. Re-runnable: every step
# overwrites its own value, so fixing a stale secret is the same command.
#
#   ./scripts/setup_github.sh                              # all defaults
#   ./scripts/setup_github.sh --yc-sa-key /tmp/ci-sa.json  # upload the SA key
#
# The same service account key (YC_SA_KEY) drives both jobs:
#   * push   -> `docker login cr.yandex -u json_key`
#   * deploy -> installs yc, builds a profile from the key, fetches kubeconfig
# A stored KUBECONFIG secret is NOT used: it would embed a local yc path and
# profile name that do not exist on a runner.
#
# Create the SA and its key once (roles: pusher to push, k8s.* to deploy):
#
#   yc iam service-account create --name url-fraud-ci-sa \
#     --folder-id b1gslcf31j2qksdat95l --description "CI push + deploy"
#   yc container registry add-access-binding --id <registry-id> \
#     --role container-registry.images.pusher \
#     --service-account-id <sa-id>
#   yc resource-manager folder add-access-binding b1gslcf31j2qksdat95l \
#     --role k8s.viewer --service-account-id <sa-id>
#   yc resource-manager folder add-access-binding b1gslcf31j2qksdat95l \
#     --role k8s.cluster-api.cluster-admin --service-account-id <sa-id>
#   yc iam key create --service-account-id <sa-id> \
#     --output /tmp/ci-sa.json --folder-id b1gslcf31j2qksdat95l
set -euo pipefail

REPO="${GITHUB_REPOSITORY:-lizkakostereva-lgtm/otus-practices}"
KUBE_CONTEXT="${KUBE_CONTEXT:-url-fraud-cluster}"
YC_REGISTRY_ID="${YC_REGISTRY_ID:-}"
YC_FOLDER_ID="${YC_FOLDER_ID:-b1gslcf31j2qksdat95l}"
YC_SA_KEY_FILE="${YC_SA_KEY_FILE:-}"

die() { echo "error: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --context) KUBE_CONTEXT="${2:?--context needs a value}"; shift 2 ;;
    --yc-registry-id) YC_REGISTRY_ID="${2:?--yc-registry-id needs a value}"; shift 2 ;;
    --yc-folder-id) YC_FOLDER_ID="${2:?--yc-folder-id needs a value}"; shift 2 ;;
    --yc-sa-key) YC_SA_KEY_FILE="${2:?--yc-sa-key needs a path}"; shift 2 ;;
    -h|--help) sed -n '2,34p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

# --- preflight ---------------------------------------------------------------

command -v gh >/dev/null 2>&1 || die "gh is not installed: brew install gh"
gh auth status >/dev/null 2>&1 || die "gh is not authenticated: gh auth login"
echo "repository: $REPO"

# --- variables ---------------------------------------------------------------

step "Setting repository variables"
gh variable set KUBE_CONTEXT --repo "$REPO" --body "$KUBE_CONTEXT"
echo "    KUBE_CONTEXT = $KUBE_CONTEXT"

gh variable set YC_FOLDER_ID --repo "$REPO" --body "$YC_FOLDER_ID"
echo "    YC_FOLDER_ID = $YC_FOLDER_ID"

# Registry id defaults to `terraform output` if not given.
if [ -z "$YC_REGISTRY_ID" ]; then
  YC_REGISTRY_ID="$(cd terraform 2>/dev/null && terraform output -raw registry_id 2>/dev/null || true)"
fi
[ -n "$YC_REGISTRY_ID" ] || die "could not derive YC_REGISTRY_ID (terraform output); set --yc-registry-id"
gh variable set YC_REGISTRY_ID --repo "$REPO" --body "$YC_REGISTRY_ID"
echo "    YC_REGISTRY_ID = $YC_REGISTRY_ID"

# --- CI service account key secret -------------------------------------------

if [ -n "$YC_SA_KEY_FILE" ]; then
  step "Uploading the CI service account key as the YC_SA_KEY secret"
  [ -f "$YC_SA_KEY_FILE" ] || die "authorized key not found: $YC_SA_KEY_FILE"
  gh secret set YC_SA_KEY --repo "$REPO" --body "$(cat "$YC_SA_KEY_FILE")"
  rm -f "$YC_SA_KEY_FILE"
  echo "    YC_SA_KEY set (source file removed)"
else
  echo "    note: no --yc-sa-key given. Both push and deploy fail on a missing YC_SA_KEY."
  echo "          Create a SA key and re-run: ./scripts/setup_github.sh --yc-sa-key /tmp/ci-sa.json"
fi

# --- verify ------------------------------------------------------------------

step "Current GitHub configuration"
echo "    variables:"
gh variable list --repo "$REPO" | sed 's/^/      /'
echo "    secrets (names only):"
gh secret list --repo "$REPO" | sed 's/^/      /'

cat <<EOF

==> Done.

One thing is outside this script's reach:

  The "Run workflow" button only appears for workflows that exist on the
  default branch. Merge the PR to main first:
    https://github.com/$REPO/pull/new/hw_09

Then: Actions -> hw_09 CI/CD -> Run workflow -> Run.
EOF
