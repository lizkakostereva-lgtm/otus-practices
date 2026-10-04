#!/usr/bin/env bash
#
# Create the docker-registry secret used to pull the image in the cluster.
#
# Two cases:
#   1. The GHCR package is PUBLIC  -> nodes pull anonymously, skip this script.
#   2. The GHCR package is PRIVATE -> run this with a PAT.
#
#   GHCR_TOKEN=<github PAT with read:packages> ./scripts/create_image_pull_secret.sh
#   GHCR_TOKEN=... ./scripts/create_image_pull_secret.sh my-secret
#
# GITHUB_USER is optional: it is derived from the origin remote when unset (same
# rule as GITHUB_OWNER in the Makefile). Set it explicitly only to override.
set -euo pipefail

REGISTRY="${REGISTRY:-ghcr.io}"
NAMESPACE="${NAMESPACE:-url-fraud}"
SECRET_NAME="${1:-ghcr-pull}"
# GHCR authenticates with the GitHub login, not the owner segment of the image
# ref, so derive it the same way GITHUB_OWNER is derived in the Makefile.
GITHUB_USER="${GITHUB_USER:-$(git config --get remote.origin.url 2>/dev/null | sed -E 's|^git@github\.com:||; s|^https?://github\.com/||; s|\.git$||' | cut -d/ -f1)}"

if [ -z "${GHCR_TOKEN:-}" ]; then
  cat <<'EOF'
error: GHCR_TOKEN is not set.

A public image does not need this secret — just make the package public
(Settings -> General -> Danger Zone -> "Change visibility" on the package).

For a private package:
  export GHCR_TOKEN=<GitHub PAT with 'read:packages' (write:packages to push)>
  ./scripts/create_image_pull_secret.sh
EOF
  exit 1
fi

if [ -z "${GITHUB_USER}" ]; then
  echo "error: cannot derive GITHUB_USER from the origin remote." >&2
  echo "       export GITHUB_USER=<your github login> and re-run." >&2
  exit 1
fi

echo "==> Creating imagePullSecret '${SECRET_NAME}' in namespace '${NAMESPACE}'"

kubectl --namespace "${NAMESPACE}" create secret docker-registry "${SECRET_NAME}" \
  --docker-server="${REGISTRY}" \
  --docker-username="${GITHUB_USER}" \
  --docker-password="${GHCR_TOKEN}" \
  --dry-run=client -o yaml | kubectl apply -f -

cat <<EOF

==> Secret created. Reference it from the Deployment:
    spec:
      template:
        spec:
          imagePullSecrets:
            - name: ${SECRET_NAME}

    ./scripts/deploy_k8s.sh <image> --with-pull-secret ${SECRET_NAME}
EOF