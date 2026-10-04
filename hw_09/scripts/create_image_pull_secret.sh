#!/usr/bin/env bash
#
# Create the docker-registry secret used to pull the image in the cluster.
#
# Two cases:
#   1. The GHCR package is PUBLIC  -> nodes pull anonymously, skip this script.
#   2. The GHCR package is PRIVATE -> run this with a PAT.
#
#   GHCR_TOKEN=<github PAT with write:packages> ./scripts/create_image_pull_secret.sh
#   GHCR_TOKEN=... ./scripts/create_image_pull_secret.sh my-secret
set -euo pipefail

REGISTRY="${REGISTRY:-ghcr.io}"
NAMESPACE="${NAMESPACE:-url-fraud}"
SECRET_NAME="${1:-ghcr-pull}"
GITHUB_USER="${GITHUB_USER:-${GITHUB_USER:-}}"

if [ -z "${GHCR_TOKEN:-}" ]; then
  cat <<'EOF'
error: GHCR_TOKEN is not set.

A public image does not need this secret — just make the package public
(Settings -> General -> Danger Zone -> "Change visibility" on the package).

For a private package:
  export GHCR_TOKEN=<GitHub PAT with 'read:packages' (write:packages to push)>
  export GITHUB_USER=<your github login>
  ./scripts/create_image_pull_secret.sh
EOF
  exit 1
fi

if [ -z "${GITHUB_USER}" ]; then
  echo "error: GITHUB_USER is not set" >&2
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