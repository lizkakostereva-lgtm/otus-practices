"""DAG: periodic retraining of the fraud detection model with MLflow tracking.

Pipeline (one temporary Data Proc cluster per run):
  1. setup               - verify Airflow variables
  2. create_cluster      - create temporary Spark cluster (Yandex Data Proc)
  3. submit_cleaning_job - clean raw fraud dataset (S3 -> Parquet -> S3)
  4. submit_training_job - train models, log to MLflow (metrics + artifacts in S3)
  5. promote_best_model  - compare metrics and promote best version to Production
  6. destroy_cluster     - always remove the cluster (trigger_rule=ALL_DONE)

MLflow server runs on a separate VM; metadata DB - Managed PostgreSQL;
model artifacts are stored in S3 (Object Storage).
"""

import json
import uuid
from datetime import datetime

import requests
from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator
from airflow.providers.yandex.operators.dataproc import (
    DataprocCreateClusterOperator,
    DataprocCreatePysparkJobOperator,
    DataprocDeleteClusterOperator,
)
from airflow.utils.trigger_rule import TriggerRule

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
CLEAN_OUTPUT_S3 = Variable.get("YC_CLEAN_OUTPUT_S3",
                               default_var=f"s3a://{YC_S3_BUCKET}/fraud_clean_parquet")
CLEANING_SCRIPT_URI = f"s3a://{YC_S3_BUCKET}/scripts/fraud_cleaning.py"
TRAIN_SCRIPT_URI = f"s3a://{YC_S3_BUCKET}/scripts/fraud_train.py"

# --- MLflow ------------------------------------------------------------------
MLFLOW_TRACKING_URI = Variable.get("MLFLOW_TRACKING_URI", default_var=None)
MLFLOW_S3_ENDPOINT = Variable.get("MLFLOW_S3_ENDPOINT_URL",
                                  default_var="https://storage.yandexcloud.net")
MLFLOW_AWS_ACCESS_KEY_ID = Variable.get("MLFLOW_AWS_ACCESS_KEY_ID", default_var=None)
MLFLOW_AWS_SECRET_ACCESS_KEY = Variable.get("MLFLOW_AWS_SECRET_ACCESS_KEY", default_var=None)
MLFLOW_EXPERIMENT = Variable.get("MLFLOW_EXPERIMENT", default_var="fraud_detection")
MLFLOW_MODEL_NAME = Variable.get("MLFLOW_MODEL_NAME", default_var="fraud-detector")
# Семплирование датасета: полные 235M строк не влезают в 3-нодный кластер по времени
# обучения. Значение можно переопределить в Airflow (0.0-1.0).
YC_SAMPLE_FRACTION = Variable.get("YC_SAMPLE_FRACTION", default_var="0.1")

# --- Общие параметры DAG -----------------------------------------------------
# retries=0: destroy_cluster (ALL_DONE) удаляет кластер сразу после падения любой
# задачи, поэтому ретрай submit-джоб без кластера обречён ("cluster not found").
default_args = {
    "owner": "student",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 0,
}

# Подключение к Yandex Cloud для операторов Data Proc (создаётся вручную один раз).
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
        "MLFLOW_TRACKING_URI": MLFLOW_TRACKING_URI,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(
            "Не заданы обязательные переменные Airflow: " + ", ".join(missing)
            + ". Задайте их в Admin -> Variables, см. README."
        )


def _list_model_versions(base_url, model_name):
    """Все версии registered model (с пагинацией)."""
    versions, page = [], None
    while True:
        params = {"name": model_name}
        if page:
            params["page_token"] = page
        resp = requests.get(f"{base_url}/api/2.0/mlflow/model-versions/search",
                            params=params, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        versions.extend(body.get("model_versions", []))
        page = body.get("next_page_token")
        if not page:
            break
    return versions


def promote_best_model():
    """Находит лучший run по best_auc и переводит его версию модели в Production."""
    base = MLFLOW_TRACKING_URI.rstrip("/")

    resp = requests.get(f"{base}/api/2.0/mlflow/experiments/get-by-name",
                        params={"experiment_name": MLFLOW_EXPERIMENT}, timeout=30)
    resp.raise_for_status()
    exp_id = resp.json()["experiment"]["experiment_id"]

    resp = requests.post(f"{base}/api/2.0/mlflow/runs/search", json={
        "experiment_ids": [exp_id],
        "max_results": 20,
        "order_by": ["metrics.best_auc DESC"],
        "filter": "attributes.status = 'FINISHED'",
    }, timeout=30)
    resp.raise_for_status()
    runs = resp.json().get("runs", [])
    if not runs:
        raise RuntimeError("Нет завершённых runs в эксперименте "
                          f"'{MLFLOW_EXPERIMENT}' - запустите тренировку.")

    best = runs[0]
    best_run_id = best["info"]["run_id"]
    best_auc = {m["key"]: m["value"] for m in best["data"]["metrics"]}.get("best_auc")
    best_model = {t["key"]: t["value"] for t in best["data"]["tags"]}.get("best_model")
    print(f"Лучший run: {best_run_id}, best_auc={best_auc}, best_model={best_model}")

    versions = _list_model_versions(base, MLFLOW_MODEL_NAME)
    target = next((v for v in versions if v.get("run_id") == best_run_id), None)
    if target is None:
        print(f"Не найдена версия модели для run {best_run_id} - регистрация пропущена.")
        return

    if target.get("current_stage") == "Production":
        print(f"Версия {target['version']} уже в Production.")
        return

    resp = requests.post(f"{base}/api/2.0/mlflow/model-versions/transition", json={
        "name": MLFLOW_MODEL_NAME,
        "version": target["version"],
        "stage": "Production",
        "archive_existing_versions": True,
    }, timeout=30)
    resp.raise_for_status()
    print(f"Версия {target['version']} (run {best_run_id}) переведена в Production, "
          f"best_auc={best_auc}.")


with DAG(
    "fraud_retrain_pipeline",
    default_args=default_args,
    description="Weekly: clean data, retrain fraud model on Dataproc, track in MLflow",
    schedule="0 5 * * 1",
    start_date=datetime(2026, 8, 1),
    catchup=False,
    tags=["spark", "dataproc", "mlflow", "retrain"],
) as dag:

    setup = PythonOperator(
        task_id="setup_connections",
        python_callable=setup_connections,
    )

    create = DataprocCreateClusterOperator(
        task_id="create_cluster",
        folder_id=YC_FOLDER_ID,
        cluster_name=f"fraud-retrain-{uuid.uuid4().hex[:8]}",
        cluster_description="Temporary Spark cluster for fraud model retraining",
        subnet_id=YC_SUBNET_ID,
        s3_bucket=YC_S3_BUCKET,
        service_account_id=DP_SA_ID,
        ssh_public_keys=YC_SSH_PUBLIC_KEY,
        zone=YC_ZONE,
        cluster_image_version="2.1",
        services=["SPARK", "HDFS", "YARN"],
        security_group_ids=[DP_SECURITY_GROUP_ID],
        properties={
            "spark:spark.executor.memory": "8g",
            "spark:spark.executor.memoryOverhead": "3g",
            "spark:spark.driver.memory": "4g",
            "spark:spark.driver.memoryOverhead": "2g",
        },
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

    submit_clean = DataprocCreatePysparkJobOperator(
        task_id="submit_cleaning_job",
        main_python_file_uri=CLEANING_SCRIPT_URI,
        args=["--input", INPUT_S3, "--output", CLEAN_OUTPUT_S3],
        connection_id=YC_SA_CONN_ID,
        cluster_id="{{ task_instance.xcom_pull(task_ids='create_cluster', key='cluster_id') }}",
    )

    train_args = [
        "--input", CLEAN_OUTPUT_S3,
        "--mlflow-tracking-uri", MLFLOW_TRACKING_URI,
        "--mlflow-s3-endpoint", MLFLOW_S3_ENDPOINT,
        "--experiment-name", MLFLOW_EXPERIMENT,
        "--model-name", MLFLOW_MODEL_NAME,
        "--sample-fraction", YC_SAMPLE_FRACTION,
        "--metrics-output", f"s3a://{YC_S3_BUCKET}/mlflow_metrics",
    ]
    # Пустые строки в args ломают сборку spark-submit на агенте Data Proc
    # (AM-контейнер падает с exit 13). Ключи необязательны: без них артефакты
    # уходят через artifact proxy Tracking Server'а, у которого свои S3-ключи.
    if MLFLOW_AWS_ACCESS_KEY_ID and MLFLOW_AWS_SECRET_ACCESS_KEY:
        train_args += [
            "--aws-access-key-id", MLFLOW_AWS_ACCESS_KEY_ID,
            "--aws-secret-access-key", MLFLOW_AWS_SECRET_ACCESS_KEY,
        ]

    submit_train = DataprocCreatePysparkJobOperator(
        task_id="submit_training_job",
        main_python_file_uri=TRAIN_SCRIPT_URI,
        args=train_args,
        connection_id=YC_SA_CONN_ID,
        cluster_id="{{ task_instance.xcom_pull(task_ids='create_cluster', key='cluster_id') }}",
    )

    promote = PythonOperator(
        task_id="promote_best_model",
        python_callable=promote_best_model,
    )

    delete = DataprocDeleteClusterOperator(
        task_id="destroy_cluster",
        trigger_rule=TriggerRule.ALL_DONE,
        cluster_id="{{ task_instance.xcom_pull(task_ids='create_cluster', key='cluster_id') }}",
    )

    setup >> create >> submit_clean >> submit_train >> promote >> delete
