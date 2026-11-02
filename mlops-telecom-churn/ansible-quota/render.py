#!/usr/bin/env python3
"""Render inventory.ini and group_vars/all.yml from terraform-quota outputs.

Usage: python3 ansible-quota/render.py [branch]

The services VM is reached through the k3s VM (ProxyCommand) because its
public IP is not routable from every client network. The same k3s VM also
publishes MLflow/Airflow on ports 5000/8080 (socat, see k3s.yml).
"""
import json
import pathlib
import subprocess
import sys

import yaml

HERE = pathlib.Path(__file__).resolve().parent
TF_DIR = HERE.parent / "terraform-quota"
BRANCH = sys.argv[1] if len(sys.argv) > 1 else "feature/mlops-diploma-deploy"
SSH_KEY = pathlib.Path.home() / ".ssh" / "mlops_diploma"

PROXY_COMMAND = (
    f'ssh -i {SSH_KEY} -o StrictHostKeyChecking=no -o BatchMode=yes '
    "-W %h:%p ubuntu@{k3s_ip}"
)


def tf_outputs():
    raw = subprocess.check_output(
        ["terraform", f"-chdir={TF_DIR}", "output", "-json"], text=True
    )
    return {k: v["value"] for k, v in json.loads(raw).items()}


def main():
    out = tf_outputs()
    services_ip = out["services_public_ip"]
    services_private_ip = out["services_private_ip"]
    k3s_ip = out["k3s_public_ip"]

    proxy = PROXY_COMMAND.format(k3s_ip=k3s_ip)

    inventory = f"""[services]
services-vm ansible_host={services_ip} ansible_user=ubuntu ansible_ssh_private_key_file={SSH_KEY} ansible_ssh_common_args='-o ProxyCommand="{proxy}"'

[k3s]
k3s-vm ansible_host={k3s_ip} ansible_user=ubuntu ansible_ssh_private_key_file={SSH_KEY}
"""
    (HERE / "inventory.ini").write_text(inventory)

    variables = {
        "repo_url": "https://github.com/lizkakostereva-lgtm/otus-practices.git",
        "repo_branch": BRANCH,
        "repo_root": "/opt/mlops/repo",
        "services_public_ip": services_ip,
        "services_private_ip": services_private_ip,
        "k3s_public_ip": k3s_ip,
        "mlflow_public_uri": f"http://{k3s_ip}:5000",
        "airflow_public_uri": f"http://{k3s_ip}:8080",
        "mlflow_internal_uri": f"http://{services_private_ip}:5000",
        "image": f"{out['registry_image']}:latest",
        "bucket_data": out["bucket_data"],
        "bucket_dags": out["bucket_dags"],
        "bucket_mlflow": out["bucket_mlflow"],
        "s3_access_key": out["s3_access_key"],
        "s3_secret_key": out["s3_secret_key"],
        "s3_raw_key": "raw/WA_Fn-UseC_-Telco-Customer-Churn.csv",
        "registry_id": out["registry_id"],
        "sa_key_json": out["sa_key_json"],
        "airflow_admin_user": "admin",
        "airflow_admin_password": "admin",
        "postgres_password": "airflow",
        "mlflow_db_password": "mlflowpass",
        "kubectl_context": "mlops-k3s",
        "monitoring_chart_version": "58.7.2",
    }

    group_vars = HERE / "group_vars"
    group_vars.mkdir(exist_ok=True)
    (group_vars / "all.yml").write_text(
        yaml.safe_dump(variables, sort_keys=False, allow_unicode=True)
    )

    print(f"inventory.ini -> services {services_ip} (via {k3s_ip}), k3s {k3s_ip}")
    print(f"MLflow:  http://{k3s_ip}:5000")
    print(f"Airflow: http://{k3s_ip}:8080")
    print(f"branch: {BRANCH}")


if __name__ == "__main__":
    main()
