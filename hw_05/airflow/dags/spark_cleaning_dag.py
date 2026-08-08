"""DAG for daily Spark cluster lifecycle and fraud dataset cleaning.

Условия ДЗ:
- ежедневный запуск по расписанию;
- создание Spark-кластера (Yandex Data Proc);
- запуск скрипта очистки датасета (PySpark, spark-submit через Yandex Airflow provider);
- удаление кластера.

Скрипт очистки и сопутствующие файлы лежат в S3 (spark-bucket-ek/scripts/).
При запуске PySpark-задания Yandex Data Proc сам копирует скрипт из S3 на кластер
(main_python_file_uri) и запускает его через spark-submit.
"""

import json
import uuid
from datetime import datetime, timedelta

from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator
from airflow.utils.trigger_rule import TriggerRule
from airflow.providers.yandex.operators.dataproc import (
    DataprocCreateClusterOperator,
    DataprocCreatePysparkJobOperator,
    DataprocDeleteClusterOperator,
)

# --- Переменные Airflow (задаются в интерфейсе, см. README) ------------------
YC_FOLDER_ID = Variable.get("YC_FOLDER_ID", default_var=None)
YC_ZONE = Variable.get("YC_ZONE", default_var="ru-central1-a")
YC_SUBNET_ID = Variable.get("YC_SUBNET_ID", default_var=None)
YC_S3_BUCKET = Variable.get("YC_S3_BUCKET", default_var="spark-bucket-ek")
YC_SSH_PUBLIC_KEY = Variable.get("YC_SSH_PUBLIC_KEY", default_var=None)
DP_SA_ID = Variable.get("DP_SA_ID", default_var=None)
DP_SECURITY_GROUP_ID = Variable.get("DP_SECURITY_GROUP_ID", default_var=None)

_DP_SA_JSON = Variable.get("DP_SA_JSON", default_var=None)
DP_SA_JSON = json.dumps(_DP_SA_JSON) if isinstance(_DP_SA_JSON, dict) else _DP_SA_JSON

INPUT_S3 = Variable.get("YC_INPUT_S3", default_var="s3a://otus-mlops-source-data/*.txt")
OUTPUT_S3 = Variable.get("YC_OUTPUT_S3", default_var=f"s3a://{YC_S3_BUCKET}/fraud_clean_parquet")
PYSPARK_SCRIPT_URI = f"s3a://{YC_S3_BUCKET}/scripts/fraud_cleaning.py"

# --- Общие параметры DAG ------------------------------------------------------
default_args = {
    "owner": "student",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=3),
}

# Подключение к Yandex Cloud для операторов Data Proc.
# В Airflow 3 доступ к БД (ORM/CLI) из DAG и задач запрещён, поэтому подключение
# создаётся вручную один раз: Admin -> Connections -> Add (см. README).
YC_SA_CONN_ID = "yc-dataproc"


def setup_connections():
    """Проверяет, что все обязательные переменные Airflow заданы."""
    required = {
        "YC_FOLDER_ID": YC_FOLDER_ID,
        "YC_SUBNET_ID": YC_SUBNET_ID,
        "YC_SSH_PUBLIC_KEY": YC_SSH_PUBLIC_KEY,
        "DP_SA_ID": DP_SA_ID,
        "DP_SA_JSON": DP_SA_JSON,
        "DP_SECURITY_GROUP_ID": DP_SECURITY_GROUP_ID,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(
            "Не заданы обязательные переменные Airflow: " + ", ".join(missing)
            + ". Задайте их в Admin -> Variables, см. README."
        )


with DAG(
    "spark_cleaning_pipeline",
    default_args=default_args,
    description="Daily: create Dataproc cluster, clean fraud dataset, destroy cluster",
    schedule="0 6 * * *",
    start_date=datetime(2026, 7, 1),
    catchup=False,
    tags=["spark", "dataproc", "cleaning"],
) as dag:

    setup = PythonOperator(
        task_id="setup_connections",
        python_callable=setup_connections,
    )

    create = DataprocCreateClusterOperator(
        task_id="create_cluster",
        folder_id=YC_FOLDER_ID,
        cluster_name=f"fraud-cleaning-{uuid.uuid4().hex[:8]}",
        cluster_description="Temporary Spark cluster for fraud data cleaning",
        subnet_id=YC_SUBNET_ID,
        s3_bucket=YC_S3_BUCKET,
        service_account_id=DP_SA_ID,
        ssh_public_keys=YC_SSH_PUBLIC_KEY,
        zone=YC_ZONE,
        cluster_image_version="2.1",
        services=["SPARK", "HDFS", "YARN"],
        security_group_ids=[DP_SECURITY_GROUP_ID],
        masternode_resource_preset="s3-c2-m8",
        masternode_disk_type="network-hdd",
        masternode_disk_size=40,
        datanode_resource_preset="s3-c4-m16",
        datanode_disk_type="network-hdd",
        datanode_disk_size=64,
        datanode_count=3,
        computenode_count=0,
        connection_id=YC_SA_CONN_ID,
    )

    submit = DataprocCreatePysparkJobOperator(
        task_id="submit_cleaning_job",
        main_python_file_uri=PYSPARK_SCRIPT_URI,
        args=["--input", INPUT_S3, "--output", OUTPUT_S3],
        connection_id=YC_SA_CONN_ID,
        cluster_id="{{ task_instance.xcom_pull(task_ids='create_cluster', key='cluster_id') }}",
    )

    delete = DataprocDeleteClusterOperator(
        task_id="destroy_cluster",
        trigger_rule=TriggerRule.ALL_DONE,
        cluster_id="{{ task_instance.xcom_pull(task_ids='create_cluster', key='cluster_id') }}",
    )

    setup >> create >> submit >> delete
