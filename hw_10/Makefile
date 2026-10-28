SHELL := /bin/bash
.DEFAULT_GOAL := help

# --- Configuration (override with env or `make VAR=...`) ---
IMAGE_TAG       ?= 1.0.0
REGISTRY        ?= cr.yandex
YC_REGISTRY_ID  ?= $(shell cd terraform 2>/dev/null && terraform output -raw registry_id 2>/dev/null || echo "")
REGISTRY_BASE   ?= $(REGISTRY)/$(YC_REGISTRY_ID)
API_IMAGE       ?= $(REGISTRY_BASE)/url-fraud-api:$(IMAGE_TAG)
RETRAIN_IMAGE   ?= $(REGISTRY_BASE)/url-fraud-retrain:$(IMAGE_TAG)
ATTACK_IMAGE    ?= $(REGISTRY_BASE)/url-fraud-attack:$(IMAGE_TAG)
MLFLOW_IMAGE    ?= $(REGISTRY_BASE)/mlflow:$(IMAGE_TAG)
PLATFORM        ?= linux/amd64

NAMESPACE       ?= url-fraud
MON_NAMESPACE   ?= monitoring
APP_PORT        ?= 8000
NODE_PORT       ?= 30080
PYTHON          ?= python3
VENV            ?= .venv
BIN             := $(VENV)/bin
SAMPLE_FRAC     ?= 0.25
KUBE_CONTEXT    ?= url-fraud-cluster
KUBECONFIG_PATH ?= $(HOME)/.kube/config-$(KUBE_CONTEXT)
API_BASE_URL    ?= http://<node-ip>:$(NODE_PORT)

# MLflow connection used by the local retrain target.
MLFLOW_TRACKING_URI ?= http://localhost:5000
MLFLOW_EXPERIMENT   ?= fraud_url_detection
MLFLOW_MODEL_NAME   ?= url-fraud-detector

# Attack knobs (passed through to the ConfigMap).
START_TPS       ?= 20
MAX_TPS         ?= 400
RAMP_SECONDS    ?= 300
DURATION        ?= 900

# The yandex provider pings its own version-control service on startup. That
# endpoint is unreachable from some networks, which only produces a cosmetic
# warning; plan/apply work regardless.
YC_TF_ENV       ?= YC_TERRAFORM_INITIALIZATION_SILENCE=true

export MODEL_PATH       ?= $(CURDIR)/models/model.joblib
export MODEL_METADATA_PATH ?= $(CURDIR)/models/metadata.json

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

# --------------------------------------------------------------- local dev ---
$(BIN)/python:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip setuptools wheel

.PHONY: install
install: $(BIN)/python ## Create .venv and install runtime + dev dependencies
	$(BIN)/pip install -r requirements-dev.txt

.PHONY: download-data
download-data: ## Download the training CSV into data/ (~22 MB, one-off)
	@mkdir -p data
	@curl -fSL --retry 3 --silent --show-error -o data/urls.csv \
	  "https://raw.githubusercontent.com/faizann24/Using-machine-learning-to-detect-malicious-URLs/master/data/data.csv"
	@ls -lh data/urls.csv

.PHONY: train
train: ## Train the model and refresh models/model.joblib + metadata.json
	$(BIN)/python -m app.train --sample-frac $(SAMPLE_FRAC)

.PHONY: retrain-check
retrain-check: ## Retrain and fail if quality regressed (used in CI)
	$(BIN)/python -m app.train --sample-frac $(SAMPLE_FRAC) --min-f1 0.70 --min-auc 0.90

.PHONY: retrain-mlflow
retrain-mlflow: ## Retrain and log metrics + model to a running MLflow (needs MLFLOW_TRACKING_URI)
	$(BIN)/python -m mlops.retrain \
	  --tracking-uri $(MLFLOW_TRACKING_URI) \
	  --experiment $(MLFLOW_EXPERIMENT) \
	  --model-name $(MLFLOW_MODEL_NAME) \
	  --data ./data/urls.csv --sample-frac $(SAMPLE_FRAC)

.PHONY: run
run: ## Run the API locally with autoreload
	$(BIN)/uvicorn app.main:app --host 127.0.0.1 --port $(APP_PORT) --reload

.PHONY: test
test: ## Run the hermetic test suite
	$(BIN)/python -m pytest

.PHONY: coverage
coverage: ## Run tests with a coverage report
	$(BIN)/python -m pytest --cov=app --cov-report=term-missing --cov-report=html
	@echo "HTML report: htmlcov/index.html"

.PHONY: lint
lint: ## Static checks (ruff)
	$(BIN)/python -m ruff check .
	$(BIN)/python -m ruff format --check .

.PHONY: fmt
fmt: ## Auto-format the code
	$(BIN)/python -m ruff format .
	$(BIN)/python -m ruff check . --fix

.PHONY: check
check: lint test ## Lint + tests (what CI runs first)

.PHONY: validate
validate: ## Validate YAML manifests and Python syntax (no cluster needed)
	$(PYTHON) -c "import glob,yaml; [list(yaml.safe_load_all(open(f))) for f in glob.glob('**/*.y*ml', recursive=True)]; print('yaml ok')"
	$(PYTHON) -m compileall -q app mlops kafka airflow/dags && echo "python ok"

# ------------------------------------------------------------------ docker ---
.PHONY: build
build: ## Build the API image for $(PLATFORM)
	docker build --platform $(PLATFORM) -t url-fraud-api:$(IMAGE_TAG) -t url-fraud-api:latest .

.PHONY: images
images: ## Build + push ALL hw_10 images (api, retrain, attack, mlflow)
	YC_REGISTRY_ID=$(YC_REGISTRY_ID) PLATFORM=$(PLATFORM) ./scripts/build_push_images.sh $(IMAGE_TAG)

.PHONY: run-docker
run-docker: ## Run the API image locally, publishing port 8000
	docker run --rm -p $(APP_PORT):8000 url-fraud-api:latest

.PHONY: smoke-local
smoke-local: ## Smoke-test the locally running container
	API_BASE_URL=http://127.0.0.1:$(APP_PORT) ./scripts/smoke_test.sh

# ------------------------------------------------------------- kubernetes ---
.PHONY: tf-init
tf-init: ## terraform init in terraform/
	cd terraform && terraform init

.PHONY: tf-plan
tf-plan: ## terraform plan
	cd terraform && $(YC_TF_ENV) terraform plan

.PHONY: tf-apply
tf-apply: ## terraform apply (creates the cluster, registry, buckets)
	cd terraform && $(YC_TF_ENV) terraform apply -auto-approve

.PHONY: tf-destroy
tf-destroy: ## terraform destroy (deletes the cluster, registry, buckets)
	cd terraform && $(YC_TF_ENV) terraform destroy -auto-approve

.PHONY: kubeconfig
kubeconfig: ## Download the cluster kubeconfig to ~/.kube/config-url-fraud-cluster
	@./scripts/get_kubeconfig.sh "$(KUBECONFIG_PATH)"

.PHONY: deploy
deploy: ## Apply the API manifests (namespace, deploy, svc, hpa, pdb, servicemonitor)
	KUBE_CONTEXT=$(KUBE_CONTEXT) ./scripts/deploy_k8s.sh "$(API_IMAGE)"

.PHONY: deploy-monitoring
deploy-monitoring: ## Helm-install kube-prometheus-stack + alerts + dashboards
	NAMESPACE=$(NAMESPACE) MON_NAMESPACE=$(MON_NAMESPACE) ./scripts/deploy_monitoring.sh

.PHONY: deploy-mlflow
deploy-mlflow: ## Deploy MLflow (Postgres backend + S3 artifacts)
	NAMESPACE=$(NAMESPACE) ./scripts/deploy_mlflow.sh

.PHONY: deploy-airflow
deploy-airflow: ## Helm-install in-cluster Airflow with git-synced DAGs
	NAMESPACE=$(NAMESPACE) ./scripts/deploy_airflow.sh

.PHONY: deploy-kafka
deploy-kafka: ## Deploy the in-cluster single-broker Kafka
	NAMESPACE=$(NAMESPACE) ./scripts/deploy_kafka.sh

.PHONY: undeploy
undeploy: ## Remove the API manifests
	kubectl --context $(KUBE_CONTEXT) -n $(NAMESPACE) delete -k k8s/ --ignore-not-found

.PHONY: status
status: ## Show app pods, services and the HPA
	kubectl --context $(KUBE_CONTEXT) -n $(NAMESPACE) get pods,svc,hpa -o wide

.PHONY: logs
logs: ## Tail the API logs
	kubectl --context $(KUBE_CONTEXT) -n $(NAMESPACE) logs -l app.kubernetes.io/name=url-fraud-api -f --tail=100

.PHONY: smoke
smoke: ## Smoke-test the deployed service (needs API_BASE_URL)
	API_BASE_URL=$(API_BASE_URL) ./scripts/smoke_test.sh

# ---------------------------------------------------------------- attack -----
.PHONY: attack
attack: ## Run the Kafka attack simulation (ramps TPS until the alert fires)
	NAMESPACE=$(NAMESPACE) KUBE_CONTEXT=$(KUBE_CONTEXT) \
	START_TPS=$(START_TPS) MAX_TPS=$(MAX_TPS) RAMP_SECONDS=$(RAMP_SECONDS) DURATION=$(DURATION) \
	./scripts/run_attack.sh

.PHONY: scale-watch
scale-watch: ## Watch HPA scaling 4 -> 6 while the attack runs
	NAMESPACE=$(NAMESPACE) ./scripts/verify_scaling.sh

.PHONY: port-forward
port-forward: ## Forward Grafana/Prometheus/Alertmanager/MLflow/Airflow to localhost
	MON_NAMESPACE=$(MON_NAMESPACE) APP_NAMESPACE=$(NAMESPACE) ./scripts/port_forward.sh

# ------------------------------------------------------------------ github ---
.PHONY: gh-setup
gh-setup: ## Configure GitHub Actions secrets/variables (needs gh)
	@./scripts/setup_github.sh

.PHONY: clean
clean: ## Remove caches and local images
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
