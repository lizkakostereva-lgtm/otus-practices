# hw_09 — Malicious URL Classifier REST API

Production-shaped ML service: a scikit-learn classifier behind a FastAPI REST
API, containerised, pushed to GHCR, deployed to a 3-node Managed Kubernetes
cluster in Yandex Cloud, and reachable from the internet.

The model answers one question: **is this URL malicious?**

```
$ curl -X POST http://<node-ip>:30080/api/v1/predict \
       -H 'Content-Type: application/json' \
       -d '{"url":"upstreams.info/wp-admin/includes/inst.exe"}'

{
  "prediction": {
    "url": "http://upstreams.info/wp-admin/includes/inst.exe",
    "label": "bad",
    "is_fraud": true,
    "probability": 0.9085,
    "threshold": 0.55,
    "model_version": "1.0.0"
  },
  "request_id": "0f3c9d1e-..."
}
```

---

## Table of contents

1. [What is inside](#what-is-inside)
2. [Quick start](#quick-start)
3. [API reference](#api-reference)
4. [Configuration](#configuration)
5. [Model and metrics](#model-and-metrics)
6. [Tests](#tests)
7. [Docker](#docker)
8. [CI/CD](#cicd)
9. [Deploy to Yandex Cloud](#deploy-to-yandex-cloud)
10. [Operations](#operations)
11. [Troubleshooting](#troubleshooting)
12. [Design decisions](#design-decisions)

---

## What is inside

```
hw_09/
├── app/
│   ├── __init__.py
│   ├── config.py          # pydantic-settings, all env vars in one place
│   ├── main.py            # FastAPI app, routes, middleware, lifespan
│   ├── predictor.py       # lazy singleton model holder + metadata
│   ├── schemas.py         # request/response contract + URL validation
│   └── train.py           # training script, writes model + metadata
├── models/
│   ├── model.joblib       # the trained pipeline (COMMITTED on purpose)
│   └── metadata.json      # params, metrics, threshold, artifact sha256
├── tests/
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_main.py
│   ├── test_predictor.py
│   ├── test_schemas.py
│   ├── test_train.py
│   └── test_acceptance_live.py   # skipped unless API_BASE_URL is set
├── k8s/
│   ├── 00-namespace.yaml
│   ├── 10-configmap.yaml
│   ├── 20-deployment.yaml
│   ├── 30-service-nodeport.yaml
│   ├── 40-ingress.yaml          # optional: Load Balancer instead of NodePort
│   ├── 50-hpa.yaml
│   ├── 60-pdb.yaml
│   └── kustomization.yaml
├── terraform/
│   ├── versions.tf        # provider pin
│   ├── providers.tf
│   ├── variables.tf
│   ├── locals.tf
│   ├── network.tf         # VPC, shared egress NAT, 3+3 subnets, SGs
│   ├── service_account.tf # control-plane and node service accounts
│   ├── kubernetes.tf      # Managed Kubernetes + 3-node group
│   ├── outputs.tf
│   └── terraform.tfvars.example
├── scripts/
│   ├── get_kubeconfig.sh
│   ├── create_image_pull_secret.sh
│   ├── deploy_k8s.sh
│   └── smoke_test.sh      # 24 end-to-end checks against a live instance
├── .github/workflows/     # (repo root) hw_09-ci-cd.yml
├── Dockerfile             # multi-stage, non-root, healthchecked
├── docker-compose.yml     # local-only stack
├── Makefile               # every command used below
├── pyproject.toml         # pytest + ruff configuration
├── requirements.txt       # pinned runtime dependencies
└── requirements-dev.txt   # pytest, coverage, httpx, ruff
```

---

## Quick start

Requirements: Python 3.12, Docker (for the container path), `yc` CLI and
Terraform 1.5+ (only for the cloud path).

```bash
cd hw_09

# 1. Virtualenv with pinned dependencies
make install

# 2. Run the API on http://127.0.0.1:8000
make run
```

The model artifact is committed, so there is nothing to train and nothing to
download for a first run. In a second terminal:

```bash
curl http://127.0.0.1:8000/health

curl -X POST http://127.0.0.1:8000/api/v1/predict \
  -H 'Content-Type: application/json' \
  -d '{"url":"upstreams.info/wp-admin/includes/inst.exe"}'

# Open the interactive docs
open http://127.0.0.1:8000/docs
```

Equivalent manual setup, if you prefer no Make:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

### Retraining

The dataset is **not** committed (22 MB). Download it once, then train:

```bash
make download-data          # -> data/urls.csv (gitignored)
make train                  # rewrites models/model.joblib + metadata.json
make retrain-check          # same, but fails if F1 < 0.70 or AUC < 0.90
```

Training the full 25% sample takes ~8 s on 2 cores and is deterministic
(`random_state=42`), so the metrics below reproduce exactly.

---

## API reference

| Method | Path                       | Purpose                                     |
| ------ | -------------------------- | ------------------------------------------- |
| GET    | `/`                        | Service banner and endpoint index           |
| GET    | `/health`                  | Health JSON, including `model_loaded`       |
| GET    | `/healthz`                 | Liveness, 200 once the process is up        |
| GET    | `/readyz`                  | Readiness, **503 until the model is loaded** |
| POST   | `/api/v1/predict`          | Classify one URL                            |
| POST   | `/api/v1/predict/batch`    | Classify up to 100 URLs                     |
| GET    | `/api/v1/model/info`       | Model version, params, metrics, threshold   |
| GET    | `/metrics`                 | Prometheus metrics                          |
| GET    | `/docs`, `/redoc`          | Swagger UI / ReDoc                          |
| GET    | `/openapi.json`            | OpenAPI schema                              |

Every response carries an `X-Request-ID` header; the same id is echoed in the
`request_id` field of prediction responses.

### POST /api/v1/predict

Request:

```jsonc
{
  "url": "upstreams.info/wp-admin/includes/inst.exe",  // required
  "threshold": 0.55                                    // optional, 0.0–1.0
}
```

Response:

```jsonc
{
  "prediction": {
    "url": "http://upstreams.info/wp-admin/includes/inst.exe",
    "label": "bad",          // raw model class
    "is_fraud": true,        // probability >= threshold
    "probability": 0.9085,
    "threshold": 0.55,
    "model_version": "1.0.0"
  },
  "request_id": "0f3c9d1e-6f2a-4a1e-9c8a-2b1f6f0d9a11"
}
```

**URL normalisation.** The training set stores bare hostnames, so a missing
scheme is not an error — `docs.python.org` becomes `http://docs.python.org`.
Schemes other than `http`/`https` are rejected with 422, as are empty hosts,
hosts containing whitespace, and anything longer than `MAX_URL_LENGTH`.
`user:pass@` is stripped before scoring, because the classifier only looks at
the authority and the path.

### POST /api/v1/predict/batch

```bash
curl -X POST http://127.0.0.1:8000/api/v1/predict/batch \
  -H 'Content-Type: application/json' \
  -d '{"urls":["upstreams.info/wp-admin/includes/inst.exe","github.com/faizann24"]}'
```

```jsonc
{
  "predictions": [ /* one object per input, same shape as above */ ],
  "count": 2,
  "request_id": "…"
}
```

Batches are limited to `MAX_BATCH_SIZE` (100) entries. The whole batch is
rejected if any single URL is invalid, so the response never mixes successes
and failures.

### Errors

Non-2xx responses use a uniform body:

```jsonc
{
  "error": "validation_error",
  "detail": "url scheme must be http or https",
  "request_id": "…"
}
```

| Status | When                                                          |
| ------ | ------------------------------------------------------------- |
| 422    | Invalid URL, batch too large, threshold outside 0–1           |
| 404    | Unknown path                                                  |
| 500    | Unexpected server error; the `request_id` appears in the logs |
| 503    | `/readyz` only, while the model is still loading              |

---

## Configuration

Every setting is an environment variable. Defaults are in `app/config.py`, and
`k8s/10-configmap.yaml` holds the production values.

| Variable                 | Default                     | Meaning                                              |
| ------------------------ | --------------------------- | ---------------------------------------------------- |
| `LOG_LEVEL`              | `INFO`                      | Root log level                                       |
| `ENVIRONMENT`            | `production`                | Free-form environment label                          |
| `SERVICE_NAME`           | `url-fraud-api`             | Reported by `/` and `/health`                        |
| `ENABLE_METRICS`         | `true`                      | Serve `/metrics`                                     |
| `TRAIN_ON_STARTUP`       | `false`                     | Train if the artifact is missing (local convenience) |
| `MODEL_PATH`             | `models/model.joblib`       | Pipeline location                                    |
| `MODEL_METADATA_PATH`    | `models/metadata.json`      | Metadata location                                    |
| `DEFAULT_THRESHOLD`      | *(empty)*                   | Overrides the metadata threshold when set            |
| `MAX_BATCH_SIZE`         | `100`                       | Max URLs per batch                                   |
| `MAX_URL_LENGTH`         | `2048`                      | Max normalised URL length                            |
| `CORS_ORIGINS`           | `*`                         | Comma-separated origins, or `*`                      |
| `RANDOM_SEED`            | `42`                        | Seed for anything stochastic                         |

**Threshold precedence.** The threshold baked into `models/metadata.json`
(`0.55`, chosen by maximising F1 on the held-out split) is used unless
`DEFAULT_THRESHOLD` is set. Per-request `threshold` wins over both. This keeps
the deployed default sane without needing a redeploy to tune it.

---

## Model and metrics

```
CountVectorizer(analyzer="char", ngram_range=(1, 3), min_df=2)
  -> RandomForestClassifier(n_estimators=120, max_depth=16,
                            min_samples_leaf=2, class_weight="balanced")
```

Character n-grams rather than word tokens: malicious URLs are mostly
homoglyphs, odd TLDs and path noise (`wp-admin/includes/inst.exe`), and a linear
model over char 1–3-grams captures that signal with no preprocessing at all.

Trained on a 25% sample of
[faizann24/Using-machine-learning-to-detect-malicious-URLs](https://github.com/faizann24/Using-machine-learning-to-detect-malicious-URLs)
(420,464 rows, labels `bad`/`good`), split 80/20 stratified:

| Metric    | Value   |
| --------- | ------- |
| Accuracy  | 0.9233  |
| Precision | 0.7962  |
| Recall    | 0.7078  |
| F1        | 0.7494  |
| ROC AUC   | 0.9473  |
| Threshold | 0.55    |
| Features  | 55,392  |

82,249 train / 20,563 test rows, 16.2% positives. AUC is the number to watch:
ranking quality is high, and the threshold is what trades precision for recall.

The error profile is the interesting part, and it is measured, not guessed.
Scoring 20,000 held-out legitimate URLs gives:

| Threshold | False-positive rate on legitimate URLs |
| --------- | ------------------------------------- |
| 0.55      | 8.0%                                  |
| 0.50      | 15.2%                                 |
| 0.40      | 59.4%                                 |

At the deployed threshold the trade is 70.8% recall for 8% false positives —
reasonable for a triage queue that a human clears. The cliff between 0.50 and
0.40 is the thing to be careful with: at 0.50 roughly half of all legitimate
URLs are flagged, because the model puts a lot of mass just above 0.5. Lower
the threshold only if you understand that.

The same model has an amusing failure mode worth knowing about:
`docs.python.org` scores **0.6392** and is therefore flagged as fraud at the
default threshold. Short, clean hostnames with unfamiliar TLDs look like
phishing to a char-n-gram model trained on 2019 data. `github.com/faizann24`
scores 0.4984 and passes only just.

`GET /api/v1/model/info` returns all of this at runtime, plus the SHA-256 of the
exact `model.joblib` being served, so you can prove which artifact a pod is
running:

```bash
curl -s http://127.0.0.1:8000/api/v1/model/info | python3 -m json.tool
```

---

## Tests

```bash
make test          # 57 passed, 9 skipped
make coverage      # HTML report in htmlcov/index.html
make lint          # ruff check + format --check
make check         # both
```

The suite is hermetic: it builds a tiny synthetic pipeline in a fixture, so it
needs no network, no dataset, and runs in under a second.

The 9 skipped tests are the live acceptance suite. They activate when
`API_BASE_URL` is set:

```bash
make acceptance API_BASE_URL=http://<node-ip>:30080
```

For a dependency-free end-to-end check of a running instance (health, both
prediction polarities, URL normalisation, batch, validation, docs, metrics —
24 assertions):

```bash
make smoke API_BASE_URL=http://<node-ip>:30080
```

---

## Docker

```bash
make build          # docker build
make run-docker     # run on http://127.0.0.1:8000
make smoke-local    # 24 checks against the container
```

Or with compose:

```bash
make compose-up
curl http://127.0.0.1:8000/health
make compose-down
```

Image design:

- **Multi-stage.** Dependencies are built into `/opt/venv` in a builder stage
  and copied into a clean runtime stage; no compiler toolchain ships.
- **Non-root.** Runs as uid/gid `10001`.
- **Pinned deps.** `requirements.txt` uses `==` so the image is reproducible.
- **Artifact baked in.** `models/model.joblib` is committed and copied into the
  image, so a pull never needs PyPI or a dataset.
- **Python 3.12**, matching the interpreter that produced the pickle.
- **One worker per container.** Concurrency comes from the Deployment's
  replicas, not `--workers` — threads inside one process would only add
  GIL contention and duplicate the 3 MB model in memory.
- **Healthcheck** against `/readyz`, mirroring the k8s probes.

### Push to GHCR

The GHCR owner is derived from the `origin` remote, so no variables are needed:

```bash
echo $GITHUB_USER   # -> lizkakostereva-lgtm (shown by `make -n push`)
make login-registry # docker login ghcr.io, reads GHCR_USER / GHCR_TOKEN
make push           # -> ghcr.io/lizkakostereva-lgtm/url-fraud-api:<sha>
```

Override explicitly if needed: `make push IMAGE_TAG=1.0.0 GITHUB_OWNER=my-org`.

> Make the package **public** in the GitHub UI (package → Settings → General →
> Change visibility) and no `imagePullSecret` is needed. If you keep it private,
> create the secret first — see [Operations](#operations).

---

## CI/CD

`.github/workflows/hw_09-ci-cd.yml` (repo root) runs five jobs:

```
lint ─┐
      ├─> build ──> push ──> deploy
test ─┘
```

| Job     | Trigger                                                     | What it does                                                              |
| ------- | ----------------------------------------------------------- | ------------------------------------------------------------------------- |
| `lint`  | PR, push to `main`, tag, manual                              | `ruff check` + `ruff format --check`                                       |
| `test`  | same                                                        | `pytest` with coverage, then a small-sample retrain as a regression guard   |
| `build` | after both                                                  | `docker buildx` build, then boot the image and call the API inside it       |
| `push`  | `main`, `v*` tags, or manual with `push_image=true`          | Build and push to GHCR with `branch`, `sha`, `latest` and semver tags      |
| `deploy`| `v*` tags, or manual with `deploy=true`                      | Apply the k8s manifests, wait for rollout, run the smoke test             |

`build` deliberately boots the container and calls `/api/v1/predict` before
anything is pushed, so a broken image never reaches the registry.

### Required GitHub configuration

Repository **Settings → Secrets and variables → Actions**:

| Name                | Kind   | Needed for                                          |
| ------------------- | ------ | --------------------------------------------------- |
| `KUBECONFIG`        | secret | `deploy` — base64 of the cluster kubeconfig         |
| `KUBE_CONTEXT`      | var    | context name, default `url-fraud-cluster`           |
| `IMAGE_PULL_SECRET` | var    | name of the existing pull secret, if the package is private |

No PAT is needed: `push` and `deploy` use the workflow's automatic
`GITHUB_TOKEN`, which already carries `packages: write` for this repository.
A local `GHCR_TOKEN` is only required for `make login-registry` on your laptop.

Create the base64 kubeconfig secret with:

```bash
base64 -w0 ~/.kube/config-url-fraud-cluster   # macOS: base64 < file
```

The `deploy` job is gated on a `production` environment, so add a required
reviewer there if you want a human in the loop.

> Manual runs: set **both** `push_image` and `deploy` — `deploy` depends on
> `push`, so a run with only `deploy=true` is skipped by design.

---

## Deploy to Yandex Cloud

The deployment is Terraform for the cluster plus plain manifests for the app.
**This creates billable resources** — the destroy command at the end removes
them.

### What gets created

```
VPC  url-fraud-net
├── shared egress gateway  url-fraud-nat      ← the default network has no route
├── route table            0.0.0.0/0 → gateway
├── 3 node subnets         10.130/10.131/10.132.0.0/24  (a/b/c)
└── 3 master subnets       10.140/10.141/10.142.0.0/24  (a/b/c)

Managed Kubernetes  url-fraud-cluster        regional, 3 masters, k8s 1.33
└── node group      url-fraud-workers       fixed_scale = 3, one per zone
    ├── security group  SSH(22), kubelet, self, egress
    └── NAT on every node                   ← required for the NodePort endpoint
```

A dedicated network, rather than the shared `default` one, because those
subnets have no route table: nodes would get no egress and could not pull the
image from GHCR.

### Step 1 — authenticate

```bash
yc init     # interactive; or: export YC_TOKEN=$(yc iam create-token)
yc version
```

> `yc config list` prints your IAM token in clear text — handy for checking the
> cloud/folder IDs, but do not paste its output into a chat or an issue.

### Step 2 — configure Terraform

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit it, or pass the values inline:

```bash
# cloud and folder IDs (this CLI has no top-level `yc cloud`/`yc zone`)
yc resource-manager cloud list
yc resource-manager folder list

# compute zones and available Kubernetes versions
yc compute zone list
yc managed-kubernetes list-versions
```

`terraform.tfvars.example` already carries the cloud/folder IDs for this
account, so a plain `cp` + `make tf-init` is enough. Confirm the zone list
contains `ru-central1-a`, `-b` and `-c` before applying.

Leave `token = ""` to reuse the `yc` CLI profile — recommended, since then no
secret ever lands in a file. `terraform.tfvars` is gitignored.

### Step 3 — plan, then apply

```bash
make tf-init      # terraform init
make tf-plan      # read the plan carefully
make tf-apply     # ~10-15 minutes
```

Afterwards Terraform prints the endpoint you need:

```bash
cd terraform && terraform output          # api_endpoint, node_public_ips, ...
cd ..
```

Nodes need 2–5 minutes to become `READY`:

```bash
kubectl --context url-fraud-cluster get nodes -w
```

### Step 4 — get a kubeconfig

```bash
make kubeconfig
kubectl --context url-fraud-cluster get nodes
```

The script uses `yc managed-kubernetes cluster get-credentials` (this CLI has no
`get-kubeconfig`), writes `~/.kube/config-url-fraud-cluster`, merges the context
into your active kubeconfig — honouring `$KUBECONFIG` if it is set — and then
verifies that a bare `kubectl` can actually see the context. From outside the
VPC it requests the **external** endpoint; use `ENDPOINT_MODE=internal` if you
run kubectl from inside the VPC.

### Step 5 — push the image

```bash
export GHCR_USER=<your-github-login>
export GHCR_TOKEN=<PAT with write:packages>

make push          # ghcr.io/<your-github-login>/url-fraud-api:<sha>

# only if the GHCR package is private
export GITHUB_USER=<your-github-login>
make pull-secret      # creates the ghcr-pull secret
```

### Step 6 — deploy the app

```bash
make deploy IMAGE_TAG=1.0.0
make status
```

Or manually:

```bash
kubectl --context url-fraud-cluster apply -f k8s/00-namespace.yaml
kubectl --context url-fraud-cluster apply -f k8s/10-configmap.yaml
kubectl --context url-fraud-cluster apply -f k8s/20-deployment.yaml
kubectl --context url-fraud-cluster -n url-fraud \
  set image deployment/url-fraud-api api=ghcr.io/<you>/url-fraud-api:1.0.0
kubectl --context url-fraud-cluster apply -f k8s/30-service-nodeport.yaml
kubectl --context url-fraud-cluster -n url-fraud rollout status deployment/url-fraud-api
```

With a private image, add `--with-pull-secret ghcr-pull`.

### Step 7 — call the public API

The Service is a `NodePort` on `30080`, so any node's public IP works:

```bash
NODE_IP=$(kubectl --context url-fraud-cluster get nodes -o \
  jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}')

curl "http://${NODE_IP}:30080/health"

curl -X POST "http://${NODE_IP}:30080/api/v1/predict" \
  -H 'Content-Type: application/json' \
  -d '{"url":"upstreams.info/wp-admin/includes/inst.exe"}'

make smoke API_BASE_URL="http://${NODE_IP}:30080"
```

### NodePort or Load Balancer?

`k8s/30-service-nodeport.yaml` is the default because it is one line and needs
no extra cloud resources. The tradeoff is that the endpoint moves if the node
does.

For a stable address with TLS and a domain, use
`k8s/40-ingress.yaml` with the `yandex-ingress` Ingress controller, which
provisions a Load Balancer with a static public IP and a certificate:

```bash
kubectl --context url-fraud-cluster apply -f k8s/40-ingress.yaml
kubectl --context url-fraud-cluster -n url-fraud get ingress -w
```

Edit the `host` in that file first. Do not apply both `30-` and `40-`.

---

## Operations

```bash
make status                       # pods, services, node ports
make logs                         # tail the API logs
make undeploy                     # delete the manifests
make tf-destroy                   # delete the cluster, VPC, and everything else
```

Useful one-liners:

```bash
# scale
kubectl --context url-fraud-cluster -n url-fraud scale deployment/url-fraud-api --replicas 4

# what is actually running
kubectl --context url-fraud-cluster -n url-fraud get deployment url-fraud-api -o yaml

# roll back to the previous image
kubectl --context url-fraud-cluster -n url-fraud rollout undo deployment/url-fraud-api

# resource usage (needs metrics-server)
kubectl --context url-fraud-cluster -n url-fraud top pods

# enable autoscaling
kubectl --context url-fraud-cluster -n url-fraud apply -f k8s/50-hpa.yaml

# traffic per class
curl -s http://$NODE_IP:30080/metrics | grep url_fraud
```

### Production hardening already in place

- `runAsNonRoot`, `readOnlyRootFilesystem`, all capabilities dropped, seccomp
  `RuntimeDefault` — the namespace enforces the `restricted` Pod Security
  Standard, so the pod would be rejected if these were removed.
- Non-root user `10001`, writable `/tmp` via `emptyDir` (required by
  `readOnlyRootFilesystem`).
- No CPU limit on purpose: throttling would add latency spikes to an inference
  call that normally takes milliseconds. Memory is capped at 512Mi.
- `startupProbe` → `livenessProbe` → `readinessProbe` chain, so a slow model
  load is never mistaken for a dead process, and `/readyz` keeps unready pods
  out of the Service endpoints.
- 2 replicas spread across nodes, `maxUnavailable: 0`, plus a PodDisruptionBudget.

### Known limitations

- The NodePort is open to `0.0.0.0/0` and the API has **no authentication**.
  That is fine for a graded homework deployment and wrong for anything real.
  Restrict `api_port`'s CIDR in `variables.tf`, or use the Ingress route.
- The model is a 2019-era URL classifier. It judges URL *strings*, so it cannot
  see redirects, page content, or newly registered domains.
- The container image itself has **never been built**: this machine has no
  Docker daemon. Everything below the image boundary — model, API, tests,
  manifests, Terraform — is verified, but treat `make build` as the first real
  test of the `Dockerfile`.

---

## Troubleshooting

| Symptom                                    | Cause and fix                                                                 |
| ----------------------------------------- | ----------------------------------------------------------------------------- |
| `failed to connect to the docker API`      | Docker daemon is not running — start Docker Desktop                             |
| `/readyz` returns 503 forever              | `models/model.joblib` missing from the image; check `kubectl logs`             |
| Pods stuck in `ImagePullBackOff`           | Wrong tag, or a private package without a pull secret (`make pull-secret`)      |
| Nodes `NotReady` right after apply         | Normal for the first 2–5 minutes; image pulls and CNI setup                    |
| `CrashLoopBackOff`                         | `kubectl -n url-fraud logs deployment/url-fraud-api`                            |
| No `ExternalIP` on nodes                   | `enable_public_ip_on_nodes = false`; re-apply or use an Ingress                  |
| Terraform: provider version mismatch       | `terraform init -upgrade` after changing `versions.tf`                          |
| `yc managed-kubernetes list-versions`      | Use a version this folder allows, then set `kubernetes_version`                 |
| Acceptance tests all skip                  | `API_BASE_URL` is not set                                                       |
| NodePort connection times out from outside | VPC firewall or corporate egress rules; test `curl` from a phone hotspot        |

---

## Design decisions

**Why a NodePort?** The assignment asks for a publicly reachable API on three
nodes. A NodePort is the cheapest way to get one and needs no extra Yandex
resources. The Ingress alternative is included for when a stable IP matters.

**Why a regional control plane?** Three masters, one per zone. A zonal control
plane would be a single point of failure for a cluster whose whole point is
three nodes.

**Why fixed scale on the node group?** `fixed_scale = 3` keeps "3 nodes"
literally true. Cluster autoscaling is available as a commented-out variable
but off by default, because an autoscaling group can end up with four or five
nodes and that contradicts the requirement.

**Why is the model committed to git?** It is 2.9 MB of deterministic output
from a script in the same repo. Committing it means the Docker build needs no
network, tests need no dataset, and a reviewer can reproduce the exact served
artifact — `metadata.json` carries its SHA-256.

**Why no authentication?** Out of scope for the assignment. The `restricted`
Pod Security Standard, non-root user, read-only root filesystem and resource
limits are all in place so that adding auth later is a routing change rather
than a hardening project.

**Why `--workers 1`?** One model per process. Replicas in the Deployment give
horizontal scale with linear memory cost; threads would duplicate the model
without using extra cores.

---

## License / dataset

The dataset belongs to
[faizann24/Using-machine-learning-to-detect-malicious-URLs](https://github.com/faizann24/Using-machine-learning-to-detect-malicious-URLs)
and is fetched at training time, not redistributed here.