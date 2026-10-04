SHELL := /bin/bash
.DEFAULT_GOAL := help

# --- Configuration (override with env or `make VAR=...`) ---
IMAGE_NAME      ?= url-fraud-api
IMAGE_TAG       ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
IMAGE           ?= $(IMAGE_NAME):$(IMAGE_TAG)
REGISTRY        ?= ghcr.io
# GHCR requires the GitHub account (or org) as the first path segment. Derive it
# from the origin remote so `make push` targets the right namespace by default.
# NB: no '#' delimiters here - make treats them as comments inside $(shell).
GITHUB_OWNER    ?= $(shell git config --get remote.origin.url 2>/dev/null | sed -E 's|^git@github\.com:||; s|^https?://github\.com/||; s|\.git$$||' | cut -d/ -f1)
PUSH_IMAGE      ?= $(REGISTRY)/$(GITHUB_OWNER)/$(IMAGE_NAME)
# Yandex Cloud nodes are amd64. Building the default platform means the image you
# test locally is the image that gets deployed. On Apple Silicon you can override
# it for faster native iteration: PLATFORM=linux/arm64 make build
PLATFORM        ?= linux/amd64
NAMESPACE       ?= url-fraud
APP_PORT        ?= 8000
NODE_PORT       ?= 30080
PYTHON          ?= python3
VENV            ?= .venv
BIN             := $(VENV)/bin
SAMPLE_FRAC     ?= 0.25
KUBE_CONTEXT    ?= $(NAMESPACE)-cluster
KUBECONFIG_PATH ?= $(HOME)/.kube/config-$(KUBE_CONTEXT)
# The yandex provider pings its own version-control service on startup. That
# endpoint is unreachable from some networks, which only produces the cosmetic
# "Cannot connect to YC tool initialization service" warning - plan/apply work
# regardless. Set YC_TF_ENV= to see it again.
YC_TF_ENV       ?= YC_TERRAFORM_INITIALIZATION_SILENCE=true

export MODEL_PATH       ?= $(CURDIR)/models/model.joblib
export MODEL_METADATA_PATH ?= $(CURDIR)/models/metadata.json

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

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
	@curl -fSL --retry 3 --silent --show-error \
	  -o data/urls.csv \
	  "https://raw.githubusercontent.com/faizann24/Using-machine-learning-to-detect-malicious-URLs/master/data/data.csv"
	@ls -lh data/urls.csv

.PHONY: train
train: ## Train the model and refresh models/model.joblib + metadata.json
	$(BIN)/python -m app.train --sample-frac $(SAMPLE_FRAC)

.PHONY: retrain-check
retrain-check: ## Retrain and fail if quality regressed (used in CI)
	$(BIN)/python -m app.train --sample-frac $(SAMPLE_FRAC) --min-f1 0.70 --min-auc 0.90

.PHONY: run
run: ## Run the API locally with autoreload
	$(BIN)/uvicorn app.main:app --host 127.0.0.1 --port $(APP_PORT) --reload

.PHONY: test
test: ## Run the hermetic test suite
	$(BIN)/python -m pytest

.PHONY: test-verbose
test-verbose: ## Run the test suite with per-test output
	$(BIN)/python -m pytest -v

.PHONY: coverage
coverage: ## Run tests with a coverage report
	$(BIN)/python -m pytest --cov=app --cov-report=term-missing --cov-report=html
	@echo "HTML report: htmlcov/index.html"

.PHONY: acceptance
acceptance: ## Acceptance tests against a live deployment (needs API_BASE_URL)
	@test -n "$(API_BASE_URL)" || { echo "set API_BASE_URL, e.g. http://1.2.3.4:30080"; exit 1; }
	API_BASE_URL=$(API_BASE_URL) $(BIN)/python -m pytest tests/test_acceptance_live.py -v

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

# ------------------------------------------------------------------ docker ---
.PHONY: build
build: ## Build the docker image for $(PLATFORM)
	docker build --platform $(PLATFORM) -t $(IMAGE) -t $(IMAGE_NAME):latest .

.PHONY: run-docker
run-docker: ## Run the image locally, publishing port 8000
	docker run --rm -p $(APP_PORT):8000 $(IMAGE_NAME):latest

.PHONY: smoke-local
smoke-local: ## Smoke-test the locally running container
	API_BASE_URL=http://127.0.0.1:$(APP_PORT) ./scripts/smoke_test.sh

.PHONY: compose-up
compose-up: ## Start the service with docker compose
	docker compose up --build -d

.PHONY: compose-down
compose-down: ## Stop the compose stack
	docker compose down -v

.PHONY: login-registry
login-registry: ## Log into the container registry (needs GHCR_TOKEN)
	@test -n "$(GHCR_TOKEN)" || { echo "export GHCR_TOKEN=<github PAT with write:packages>"; exit 1; }
	@echo "$$GHCR_TOKEN" | docker login $(REGISTRY) -u "$(GHCR_USER)" --password-stdin

.PHONY: push
push: ## Push the image to GHCR (owner derived from git remote)
	@$(MAKE) --no-print-directory login-registry
	docker tag $(IMAGE) $(PUSH_IMAGE):$(IMAGE_TAG)
	docker push $(PUSH_IMAGE):$(IMAGE_TAG)

# ------------------------------------------------------------- kubernetes ---
.PHONY: tf-init
tf-init: ## terraform init in terraform/
	cd terraform && terraform init

.PHONY: tf-plan
tf-plan: ## terraform plan
	cd terraform && $(YC_TF_ENV) terraform plan

.PHONY: tf-apply
tf-apply: ## terraform apply (creates the 3-node k8s cluster)
	cd terraform && $(YC_TF_ENV) terraform apply -auto-approve

.PHONY: tf-destroy
tf-destroy: ## terraform destroy (deletes the cluster and network)
	cd terraform && $(YC_TF_ENV) terraform destroy -auto-approve

.PHONY: kubeconfig
kubeconfig: ## Download the cluster kubeconfig to ~/.kube/config-url-fraud-cluster
	@./scripts/get_kubeconfig.sh "$(KUBECONFIG_PATH)"

.PHONY: pull-secret
pull-secret: ## Create the GHCR imagePullSecret in the cluster
	@./scripts/create_image_pull_secret.sh

.PHONY: deploy
deploy: ## Apply the k8s manifests for the current image tag
	./scripts/deploy_k8s.sh "$(PUSH_IMAGE):$(IMAGE_TAG)"

.PHONY: undeploy
undeploy: ## Remove the k8s manifests
	kubectl --context $(KUBE_CONTEXT) -n $(NAMESPACE) delete -f k8s/ --ignore-not-found

.PHONY: status
status: ## Show pods, services and public endpoint
	kubectl --context $(KUBE_CONTEXT) -n $(NAMESPACE) get pods,svc -o wide

.PHONY: logs
logs: ## Tail the API logs
	kubectl --context $(KUBE_CONTEXT) -n $(NAMESPACE) logs -l app.kubernetes.io/name=$(IMAGE_NAME) -f --tail=100

.PHONY: smoke
smoke: ## Smoke-test the deployed service (needs API_BASE_URL)
	API_BASE_URL=$(API_BASE_URL) ./scripts/smoke_test.sh

.PHONY: clean
clean: ## Remove caches and local images
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +