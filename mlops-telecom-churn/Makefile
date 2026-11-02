# MLOps Telecom Churn - Makefile (Yandex Cloud + Terraform + Airflow)

.PHONY: help setup init-data preprocess train evaluate api build up-local down-local test lint dvc-init dvc-repro promote local-pipeline tf-init tf-plan tf-apply tf-destroy sync-dags k8s-deploy k8s-delete load-test clean

help:
	@echo "MLOps Telecom Churn (YC + Terraform + Airflow):"
	@echo "  make setup         - Install local deps"
	@echo "  make up-local      - MinIO + MLflow locally"
	@echo "  make local-pipeline - preprocess -> train -> evaluate -> promote"
	@echo "  make api           - Run FastAPI"
	@echo "  make build         - Build API image"
	@echo "  make test          - Run tests"
	@echo "  make tf-init       - terraform init"
	@echo "  make tf-plan       - terraform plan"
	@echo "  make tf-apply      - terraform apply (VPC, S3, Airflow, K8s)"
	@echo "  make tf-destroy    - terraform destroy"
	@echo "  make sync-dags     - Upload DAGs to S3 bucket for Managed Airflow"
	@echo "  make k8s-deploy    - Deploy API to K8s"
	@echo "  make k8s-delete    - Delete API from K8s"
	@echo "  make load-test     - Load test instructions"

setup:
	pip install -r requirements.txt

init-data:
	python scripts/download_data.py

preprocess:
	python -m src.data.preprocess

train:
	python -m src.train.train

evaluate:
	python -m src.evaluate.evaluate

promote:
	python scripts/promote_model.py

local-pipeline: preprocess train evaluate promote

api:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

up-local:
	docker compose up -d

down-local:
	docker compose down

build:
	docker build -t mlops-telecom-churn-api:latest -f docker/api/Dockerfile .

test:
	pytest -v

lint:
	ruff check . || true

dvc-init:
	dvc init -f
	dvc remote add -d yandex s3://$(BUCKET_DATA)/dvc 2>/dev/null || true

dvc-repro:
	dvc repro

tf-init:
	cd terraform && terraform init

tf-plan:
	cd terraform && terraform plan

tf-apply:
	cd terraform && terraform apply

tf-destroy:
	cd terraform && terraform destroy

sync-dags:
	aws s3 sync airflow/dags/ s3://$(BUCKET_DAGS)/dags/ --endpoint-url=https://storage.yandexcloud.net

k8s-deploy:
	kubectl apply -f k8s/namespace.yaml
	kubectl apply -f k8s/configmap.yaml
	kubectl apply -f k8s/deployment.yaml
	kubectl apply -f k8s/service.yaml
	kubectl apply -f k8s/hpa.yaml
	kubectl apply -f k8s/servicemonitor.yaml
	kubectl apply -f k8s/cronjob-retrain.yaml

k8s-delete:
	kubectl delete -f k8s/ || true

load-test:
	@echo "For HPA demo: hey -n 2000 -c 20 http://<NODE_IP>:30080/health"
	@echo "or: kubectl run -it --rm load-test --image=williamyeh/hey -- -n 2000 -c 20 http://churn-api.mlops-churn.svc.cluster.local:8000/health"

clean:
	rm -rf __pycache__ .pytest_cache .ruff_cache
	find . -name "*.pyc" -delete

ansible-prep:
	@cp -n ansible/hosts.ini.example ansible/hosts.ini || true
	@cp -n ansible/vars.yml.example ansible/vars.yml || true
	@echo "Edit ansible/hosts.ini and ansible/vars.yml with IPs and keys from terraform output"

provision-mlflow:
	cd ansible && ansible-playbook -i hosts.ini deploy_postgres.yml
	cd ansible && ansible-playbook -i hosts.ini deploy_mlflow.yml

monitoring-install:
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts 2>/dev/null || true
	helm repo update
	kubectl create namespace monitoring 2>/dev/null || true
	helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack -n monitoring -f monitoring/kube-prometheus-stack-values.yaml --version 58.7.2

sync-dags:
	@test -n "$$BUCKET_DAGS" || (echo "Set BUCKET_DAGS"; exit 1)
	aws s3 sync airflow/dags/ s3://$$BUCKET_DAGS/dags/ --endpoint-url=https://storage.yandexcloud.net
