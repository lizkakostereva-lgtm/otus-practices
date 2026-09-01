#!/usr/bin/env python3
"""Генератор Extra JSON для подключения 'yc-dataproc' в Airflow.

Собирает authorized key сервисного аккаунта airflow-sa из outputs Terraform
(terraform output airflow_sa_auth_key_*) и ваш публичный SSH-ключ, после чего
печатает JSON для поля Extra в Admin -> Connections.

Запуск:
    python3 airflow/make_connection_extra.py [--tf-dir terraform]
                                        [--ssh-public-key ~/.ssh/id_ed25519.pub]
    # или
    make conn-extra
"""

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def tf_output(tf_dir, name):
    proc = subprocess.run(
        ["terraform", "output", "-raw", name],
        cwd=tf_dir, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"terraform output {name} завершился ошибкой: {proc.stderr.strip()}")
    return proc.stdout.strip()


def read_ssh_public_key(value):
    path = Path(value).expanduser()
    if path.exists():
        return path.read_text().strip()
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf-dir",
                        default=str(Path(__file__).resolve().parents[1] / "terraform"))
    parser.add_argument("--ssh-public-key", default="~/.ssh/id_ed25519.pub")
    args = parser.parse_args()

    key_id = tf_output(args.tf_dir, "airflow_sa_auth_key_id")
    public_key = tf_output(args.tf_dir, "airflow_sa_auth_key_public")
    private_key = tf_output(args.tf_dir, "airflow_sa_auth_key_private")
    sa_id = tf_output(args.tf_dir, "airflow_service_account_id")

    authorized_key = {
        "id": key_id,
        "service_account_id": sa_id,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "key_algorithm": "RSA_2048",
        "public_key": public_key,
        "private_key": private_key,
    }

    extra = {
        "public_ssh_key": read_ssh_public_key(args.ssh_public_key),
        "service_account_json": json.dumps(authorized_key),
    }

    print("=== Подключение Airflow: yc-dataproc ===")
    print("Connection Id   : yc-dataproc")
    print("Connection Type : yandexcloud (Yandex Cloud)")
    print()
    print("Extra (JSON) - вставьте целиком в поле Extra:")
    print(json.dumps(extra, ensure_ascii=False, indent=2))
    print()
    print("Подсказка: JSON автоматически сохраняется как строка, поэтому в поле")
    print("Extra вставляйте этот вывод целиком (без ручного экранирования).")


if __name__ == "__main__":
    main()
