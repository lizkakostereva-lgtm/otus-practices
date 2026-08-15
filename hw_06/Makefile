# Makefile для hw_06 — инфраструктура, Ansible, загрузка DAG, MLflow.
#
# Запуск всех команд производится из каталога hw_06.
# Для aws-команд (sync) предварительно экспортируйте ключи:
#   export AWS_ACCESS_KEY_ID=<key_id> AWS_SECRET_ACCESS_KEY=<secret>
#
# Список целей: make help

SHELL := /bin/bash

TF_DIR      := terraform
ANSIBLE_DIR := ansible
AIRFLOW_DIR := airflow

SSH_KEY     ?= ~/.ssh/id_ed25519
SSH_PUB_KEY ?= ~/.ssh/id_ed25519.pub

AWS_ENDPOINT_URL ?= https://storage.yandexcloud.net
DAGS_BUCKET      ?= airflow-dags-bucket-ek
SCRIPTS_BUCKET   ?= spark-bucket-ek

SUBNET_ID ?= $(shell grep -E '^[[:space:]]*subnet_id' $(TF_DIR)/terraform.tfvars 2>/dev/null | head -1 | sed -E 's/.*=[[:space:]]*"?([^" ]+)"?.*/\1/')

.PHONY: help init plan apply outputs nat status \
        ansible-prep provision conn-extra \
        sync-dags sync-scripts sync \
        ui health destroy

help:
	@echo "Цели Makefile:"
	@echo "  make init           terraform init"
	@echo "  make plan           terraform plan"
	@echo "  make apply          terraform apply"
	@echo "  make outputs        вывод всех output terraform (IP ВМ, ключи, URI MLflow)"
	@echo "  make nat            привязать NAT route table к подсети (subnet_id из tfvars)"
	@echo "  make status         список ресурсов terraform в state"
	@echo "  make ansible-prep   создать hosts.ini и vars.yml из примеров"
	@echo "  make provision      запустить Ansible: deploy_postgres.yml -> deploy_mlflow.yml"
	@echo "  make conn-extra     сгенерировать Extra JSON для подключения yc-dataproc"
	@echo "  make sync           загрузить DAG и скрипты в S3"
	@echo "  make sync-dags      только DAG -> airflow-dags-bucket"
	@echo "  make sync-scripts   только скрипты -> spark-bucket/scripts"
	@echo "  make ui             SSH-туннель к MLflow UI (http://localhost:5000)"
	@echo "  make health         проверить MLflow /health по SSH на mlflow-vm"
	@echo "  make destroy        terraform destroy"
	@echo
	@echo "Пример: make apply && make nat && make outputs && make ansible-prep"

init:
	cd $(TF_DIR) && terraform init

plan:
	cd $(TF_DIR) && terraform plan

apply:
	cd $(TF_DIR) && terraform apply
	@echo
	@echo "Далее выполните:"
	@echo "  1) make nat             - подключить NAT к подсети (обязательно)"
	@echo "  2) make outputs         - посмотреть IP ВМ и ключи"
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
	@test -f $(ANSIBLE_DIR)/hosts.ini || cp $(ANSIBLE_DIR)/hosts.ini.example $(ANSIBLE_DIR)/hosts.ini
	@test -f $(ANSIBLE_DIR)/vars.yml || cp $(ANSIBLE_DIR)/vars.yml.example $(ANSIBLE_DIR)/vars.yml
	@echo "Созданы $(ANSIBLE_DIR)/hosts.ini и $(ANSIBLE_DIR)/vars.yml. Заполните их (IP ВМ, пароли, ключи mlflow-sa)."

provision: ansible-prep
	ansible-playbook -i $(ANSIBLE_DIR)/hosts.ini $(ANSIBLE_DIR)/deploy_postgres.yml --key-file $(SSH_KEY)
	ansible-playbook -i $(ANSIBLE_DIR)/hosts.ini $(ANSIBLE_DIR)/deploy_mlflow.yml --key-file $(SSH_KEY)

conn-extra:
	python3 $(AIRFLOW_DIR)/make_connection_extra.py --ssh-public-key $(SSH_PUB_KEY)

sync-dags:
	aws s3 sync $(AIRFLOW_DIR)/dags/ s3://$(DAGS_BUCKET)/ --endpoint-url=$(AWS_ENDPOINT_URL) --delete --exclude "*" --include "*.py"

sync-scripts:
	aws s3 sync $(AIRFLOW_DIR)/scripts/ s3://$(SCRIPTS_BUCKET)/scripts/ --endpoint-url=$(AWS_ENDPOINT_URL) --delete

sync: sync-dags sync-scripts

ui:
	@IP=$$(cd $(TF_DIR) && terraform output -raw mlflow_vm_public_ip); \
	echo "Туннель к MLflow UI: http://localhost:5000 (Ctrl+C для выхода)"; \
	ssh -i $(SSH_KEY) -N -L 5000:localhost:5000 ubuntu@$$IP

health:
	@IP=$$(cd $(TF_DIR) && terraform output -raw mlflow_vm_public_ip); \
	ssh -i $(SSH_KEY) ubuntu@$$IP 'curl -s http://127.0.0.1:5000/health'

destroy:
	cd $(TF_DIR) && terraform destroy
