# Makefile для hw_08 — online ML-инференс через Kafka + Spark Structured
# Streaming + MLflow Model Registry (использует инфраструктуру hw_07).
#
# Запуск всех команд — из каталога hw_08.
# Для aws-команд (sync) предварительно экспортируйте ключи:
#   export AWS_ACCESS_KEY_ID=<key_id> AWS_SECRET_ACCESS_KEY=<secret>
#
# Список целей: make help

SHELL := /bin/bash

TF_DIR      := terraform
ANSIBLE_DIR := ansible
AIRFLOW_DIR := airflow

SSH_KEY    ?= ~/.ssh/id_ed25519
SSH_PUB_KEY ?= ~/.ssh/id_ed25519.pub

AWS_ENDPOINT_URL ?= https://storage.yandexcloud.net
DAGS_BUCKET      ?= airflow-dags-bucket-ek
SCRIPTS_BUCKET   ?= spark-bucket-ek

SUBNET_ID ?= $(shell grep -E '^[[:space:]]*subnet_id' $(TF_DIR)/terraform.tfvars 2>/dev/null | head -1 | sed -E 's/.*=[[:space:]]*"?([^" ]+)"?.*/\1/')

# Ansible-плейбуки переиспользуются из hw_07, но inventory и переменные
# берутся из актуального hw_08/ansible (текущие IP/ключи).
HW07_ANSIBLE := ../hw_07/ansible

.PHONY: help init plan apply outputs nat status \
        ansible-prep provision conn-extra \
        sync-dags sync-scripts sync \
        ui health test-kafka perf-test destroy

help:
	@echo "Цели Makefile (hw_08 — Kafka + Spark Streaming + MLflow):"
	@echo "  make init           terraform init"
	@echo "  make plan           terraform plan"
	@echo "  make apply          terraform apply (включая Managed Kafka)"
	@echo "  make outputs        вывод всех output terraform (IP ВМ, ключи, URI MLflow, Kafka)"
	@echo "  make nat            привязать NAT route table к подсети (subnet_id из tfvars)"
	@echo "  make status         список ресурсов terraform в state"
	@echo "  make ansible-prep   проверить hosts.ini и vars.yml в $(ANSIBLE_DIR) (IP из make outputs)"
	@echo "  make provision      Playbooks из hw_07 + inventory/vars из $(ANSIBLE_DIR): deploy_postgres.yml -> deploy_mlflow.yml"
	@echo "  make conn-extra     сгенерировать Extra JSON для подключения yc-dataproc"
	@echo "  make sync           загрузить DAG и скрипты в S3"
	@echo "  make sync-dags      только DAG -> airflow-dags-bucket"
	@echo "  make sync-scripts   только скрипты -> spark-bucket/scripts"
	@echo "  make ui             SSH-туннель к MLflow UI (http://localhost:5000)"
	@echo "  make health         проверить MLflow /health по SSH на mlflow-vm"
	@echo "  make test-kafka     показать Kafka bootstrap/topics из Terraform state"
	@echo "  make perf-test      напечатать инструкцию по запуску нагрузочного теста TPS"
	@echo "  make destroy        terraform destroy (полное удаление, включая Kafka)"
	@echo
	@echo "Пример: make apply && make nat && make outputs && make ansible-prep && make provision"

init:
	cd $(TF_DIR) && terraform init

plan:
	cd $(TF_DIR) && terraform plan

apply:
	cd $(TF_DIR) && terraform apply
	@echo
	@echo "Далее выполните:"
	@echo "  1) make nat             - подключить NAT к подсети (обязательно)"
	@echo "  2) make outputs         - IP ВМ, ключи, URI MLflow, bootstrap Kafka"
	@echo "  3) make ansible-prep    - затем заполнить $(ANSIBLE_DIR)/hosts.ini и vars.yml"
	@echo "  4) make provision       - установить PostgreSQL и MLflow на ВМ"
	@echo "  5) make conn-extra      - Extra JSON для подключения yc-dataproc"
	@echo "  6) make sync            - загрузить DAG и скрипты в S3"

outputs:
	cd $(TF_DIR) && terraform output

nat:
	@test -n "$(SUBNET_ID)" || (echo "Не найден subnet_id в $(TF_DIR)/terraform.tfvars. Задайте вручную: make nat SUBNET_ID=<id>"; exit 1)
	yc vpc subnet update $(SUBNET_ID) --route-table-id $$(cd $(TF_DIR) && terraform output -raw nat_rt_id)

status:
	cd $(TF_DIR) && terraform state list

ansible-prep:
	@test -f $(ANSIBLE_DIR)/hosts.ini || (echo "Сначала заполните $(ANSIBLE_DIR)/hosts.ini (IP ВМ из make outputs)"; exit 1)
	@test -f $(ANSIBLE_DIR)/vars.yml || (echo "Сначала заполните $(ANSIBLE_DIR)/vars.yml"; exit 1)
	@echo "Inventory: $(ANSIBLE_DIR)/hosts.ini; vars: $(ANSIBLE_DIR)/vars.yml; playbooks из hw_07: $(HW07_ANSIBLE)"

provision: ansible-prep
	ansible-playbook -i $(ANSIBLE_DIR)/hosts.ini $(HW07_ANSIBLE)/deploy_postgres.yml --key-file $(SSH_KEY) -e @$(ANSIBLE_DIR)/vars.yml --ssh-common-args="-o StrictHostKeyChecking=accept-new"
	ansible-playbook -i $(ANSIBLE_DIR)/hosts.ini $(HW07_ANSIBLE)/deploy_mlflow.yml --key-file $(SSH_KEY) -e @$(ANSIBLE_DIR)/vars.yml --ssh-common-args="-o StrictHostKeyChecking=accept-new"

conn-extra:
	python3 $(AIRFLOW_DIR)/make_connection_extra.py --ssh-public-key $(SSH_PUB_KEY)

sync-dags:
	aws s3 sync $(AIRFLOW_DIR)/dags/ s3://$(DAGS_BUCKET)/ --endpoint-url=$(AWS_ENDPOINT_URL) --delete --exclude "*" --include "*.py"

sync-scripts:
	aws s3 sync $(AIRFLOW_DIR)/scripts/ s3://$(SCRIPTS_BUCKET)/scripts/ --endpoint-url=$(AWS_ENDPOINT_URL) --delete --exclude "__pycache__/*" --exclude "*.pyc"

sync: sync-dags sync-scripts

ui:
	@IP=$$(cd $(TF_DIR) && terraform output -raw mlflow_vm_public_ip); \
	echo "Туннель к MLflow UI: http://localhost:5000 (Ctrl+C для выхода)"; \
	ssh -i $(SSH_KEY) -N -L 5000:localhost:5000 ubuntu@$$IP

health:
	@IP=$$(cd $(TF_DIR) && terraform output -raw mlflow_vm_public_ip); \
	ssh -i $(SSH_KEY) ubuntu@$$IP 'curl -s http://127.0.0.1:5000/health'

test-kafka:
	@cd $(TF_DIR) && terraform output kafka_bootstrap_servers && \
	echo "Топики: $(shell cd $(TF_DIR) && terraform output kafka_input_topic) / $(shell cd $(TF_DIR) && terraform output kafka_output_topic)"

perf-test:
	@echo "========== Как измерить точку перегиба (tipping point) =========="
	@echo "1. Задайте в Airflow Admin -> Variables:"
	@echo "     STREAMING_TPS            = 50        (начните с 50)"
	@echo "     STREAMING_MESSAGES       = 100000"
	@echo "     STREAMING_DURATION_SECONDS = 300"
	@echo "2. Запустите DAG fraud_streaming_pipeline (Schedule = None)."
	@echo "3. Повторяйте, увеличивая STREAMING_TPS: 50, 100, 200, 400."
	@echo "4. Смотрите метрики эксперимента 'fraud_streaming' в MLflow и CSV:"
	@echo "     s3://spark-bucket-ek/kafka_test_metrics/{producer,consumer}_<run>.csv"
	@echo "   Точка перегиба: consumer_*.csv consumed < produced и рост avg_batch_processing_ms."

destroy:
	cd $(TF_DIR) && terraform destroy