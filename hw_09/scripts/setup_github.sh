#!/usr/bin/env bash
#
# One-time GitHub configuration for the hw_09 pipeline.
#
# Everything the `deploy` job needs, set through the gh CLI instead of by hand in
# Settings -> Secrets and variables -> Actions. Re-runnable: every step overwrites
# its own value, so fixing a stale secret is the same command again.
#
#   ./scripts/setup_github.sh                     # vars + kubeconfig secret
#   ./scripts/setup_github.sh --no-kubeconfig     # CI only (no cluster yet)
#   ./scripts/setup_github.sh --pull-secret ghcr-pull   # private GHCR package
#
# Private GHCR package also needs a PAT:
#   export GHCR_TOKEN=<PAT with read:packages>  GITHUB_USER=<github login>
# The alternative is a public package, which needs no secret at all — see the
# README.
set -euo pipefail

REPO="${GITHUB_REPOSITORY:-lizkakostereva-lgtm/otus-practices}"
KUBE_CONTEXT="${KUBE_CONTEXT:-url-fraud-cluster}"
KUBECONFIG_FILE="${KUBECONFIG_FILE:-$HOME/.kube/config-url-fraud-cluster}"
PULL_SECRET=""
SET_KUBECONFIG=1

die() { echo "error: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --no-kubeconfig) SET_KUBECONFIG=0; shift ;;
    --pull-secret) PULL_SECRET="${2:?--pull-secret needs a value}"; shift 2 ;;
    --kubeconfig) KUBECONFIG_FILE="${2:?--kubeconfig needs a value}"; shift 2 ;;
    --context) KUBE_CONTEXT="${2:?--context needs a value}"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

if [ -n "$PULL_SECRET" ]; then
  gh variable set IMAGE_PULL_SECRET --repo "$REPO" --body "$PULL_SECRET"
  echo "    IMAGE_PULL_SECRET = $PULL_SECRET"
fi

# --- kubeconfig secret -------------------------------------------------------

if [ "$SET_KUBECONFIG" -eq 1 ]; then
  step "Encoding kubeconfig as the KUBECONFIG secret"
  [ -f "$KUBECONFIG_FILE" ] || die "kubeconfig not found: $KUBECONFIG_FILE
       run 'make kubeconfig' once the cluster is READY, or pass --kubeconfig PATH,
       or use --no-kubeconfig to set up CI only."

  # tr -d '\n' instead of base64 -w0: GNU coreutils and BSD base64 disagree on
  # the flag, and GitHub rejects newlines in a secret created this way.
  B64="$(base64 < "$KUBECONFIG_FILE" | tr -d '\n')"
  gh secret set KUBECONFIG --repo "$REPO" --body "$B64"
  unset B64
  echo "    KUBECONFIG set from $KUBECONFIG_FILE ($(wc -c < "$KUBECONFIG_FILE" | tr -d ' ') bytes)"

  # Fail here rather than three minutes into a rollout.
  if command -v kubectl >/dev/null 2>&1 \
     && kubectl config get-contexts -o name 2>/dev/null | grep -qx "$KUBE_CONTEXT"; then
    step "Checking the context name matches what CI will send"
    echo "    ok: '$KUBE_CONTEXT' exists locally"
  else
    echo "    note: context '$KUBE_CONTEXT' not found in the local kubeconfig."
    echo "          The deploy job will fail its context check if the name is wrong."
  fi
else
  echo
  echo "==> Skipping the KUBECONFIG secret (--no-kubeconfig)"
fi

# --- private package pull secret --------------------------------------------

if [ -n "$PULL_SECRET" ]; then
  step "Creating the imagePullSecret in the cluster"
  command -v kubectl >/dev/null 2>&1 || die "kubectl is required to create the pull secret"
  kubectl config get-contexts -o name 2>/dev/null | grep -qx "$KUBE_CONTEXT" \
    || die "context '$KUBE_CONTEXT' not found locally; run 'make kubeconfig' first"
  [ -n "${GHCR_TOKEN:-}" ] || die "GHCR_TOKEN is not set (needs read:packages)"
  [ -n "${GITHUB_USER:-}" ] || die "GITHUB_USER is not set (your GitHub login)"
  KUBECONFIG_PATH="$KUBECONFIG_FILE" \
    KUBE_CONTEXT="$KUBE_CONTEXT" \
    ./scripts/create_image_pull_secret.sh "$PULL_SECRET"
fi

# --- verify ------------------------------------------------------------------

step "Current GitHub configuration"
echo "    variables:"
gh variable list --repo "$REPO" | sed 's/^/      /'
echo "    secrets (names only):"
gh secret list --repo "$REPO" | sed 's/^/      /'

cat <<EOF

==> Done.

Two things are outside this script's reach:

  1. The "Run workflow" button only appears for workflows that exist on the
     default branch. Merge the PR to main first:
       https://github.com/$REPO/pull/new/hw_09
     (check the commit list - the branch carries an unrelated hw_08 commit)

  2. If the GHCR package is private and no --pull-secret was given, the deploy
     will sit in ImagePullBackOff. Either make the package public
     (package -> Settings -> Change visibility -> Public) or re-run with
     --pull-secret.

Then: Actions -> hw_09 CI/CD -> Run workflow -> Run.
EOF
