"""DAG: periodic retraining + A/B validation of the fraud detection model.

Pipeline (one temporary Data Proc cluster per run):
  1. setup                 - verify Airflow variables
  2. create_cluster        - create temporary Spark cluster (Yandex Data Proc)
  3. submit_cleaning_job   - clean raw fraud dataset (S3 -> Parquet -> S3)
  4. submit_training_job   - train candidate model, log to MLflow (metrics+artifacts in S3)
  5. submit_ab_test_job    - A/B validation: bootstrap + statistical test of the
                             candidate vs the current Production (champion) model,
                             log A/B metrics to MLflow, write decision to S3
  6. apply_ab_decision     - read the decision and transition the candidate to
                             Production if the improvement is statistically significant
  7. destroy_cluster       - always remove the cluster (trigger_rule=ALL_DONE)

MLflow server runs on a separate VM; metadata DB - Managed PostgreSQL/VM;
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
# Folder "default" Cloud Logging group; pinned on the Data Proc cluster so job
# failures emit readable logs (overrides YC's inconsistent auto-attach).
YC_LOG_GROUP_ID = Variable.get("YC_LOG_GROUP_ID",
                               default_var="e23nv5lr0f4ismcg2oij")

_DP_SA_JSON = Variable.get("DP_SA_JSON", default_var=None)
DP_SA_JSON = json.dumps(_DP_SA_JSON) if isinstance(_DP_SA_JSON, dict) else _DP_SA_JSON

INPUT_S3 = Variable.get("YC_INPUT_S3", default_var="s3a://otus-mlops-source-data/*.txt")
CLEAN_OUTPUT_S3 = Variable.get("YC_CLEAN_OUTPUT_S3",
                               default_var=f"s3a://{YC_S3_BUCKET}/fraud_clean_parquet")
CLEANING_SCRIPT_URI = f"s3a://{YC_S3_BUCKET}/scripts/fraud_cleaning.py"
TRAIN_SCRIPT_URI = f"s3a://{YC_S3_BUCKET}/scripts/fraud_train.py"
AB_TEST_SCRIPT_URI = f"s3a://{YC_S3_BUCKET}/scripts/fraud_ab_test.py"

# --- MLflow ------------------------------------------------------------------
MLFLOW_TRACKING_URI = Variable.get("MLFLOW_TRACKING_URI", default_var=None)
MLFLOW_S3_ENDPOINT = Variable.get("MLFLOW_S3_ENDPOINT_URL",
                                  default_var="https://storage.yandexcloud.net")
MLFLOW_AWS_ACCESS_KEY_ID = Variable.get("MLFLOW_AWS_ACCESS_KEY_ID", default_var=None)
MLFLOW_AWS_SECRET_ACCESS_KEY = Variable.get("MLFLOW_AWS_SECRET_ACCESS_KEY", default_var=None)
MLFLOW_EXPERIMENT = Variable.get("MLFLOW_EXPERIMENT", default_var="fraud_detection")
MLFLOW_AB_EXPERIMENT = Variable.get("MLFLOW_AB_EXPERIMENT", default_var="fraud_ab_testing")
MLFLOW_MODEL_NAME = Variable.get("MLFLOW_MODEL_NAME", default_var="fraud-detector")
YC_SAMPLE_FRACTION = Variable.get("YC_SAMPLE_FRACTION", default_var="0.05")
AB_BOOTSTRAP_ITERATIONS = Variable.get("AB_BOOTSTRAP_ITERATIONS", default_var="200")
AB_ALPHA = Variable.get("AB_ALPHA", default_var="0.01")
AB_EFFECT_THRESHOLD = Variable.get("AB_EFFECT_THRESHOLD", default_var="0.2")
AB_DECISION_OUTPUT = Variable.get("AB_DECISION_OUTPUT",
                                  default_var=f"s3a://{YC_S3_BUCKET}/ab_validation")

# --- Общие параметры DAG -----------------------------------------------------
default_args = {
    "owner": "student",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 0,
}

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


def _transition_to_production(base, model_name, version):
    """Переводит версию registered model в Production (архивируя прежние)."""
    resp = requests.post(f"{base}/api/2.0/mlflow/model-versions/transition", json={
        "name": model_name,
        "version": str(version),
        "stage": "Production",
        "archive_existing_versions": True,
    }, timeout=30)
    resp.raise_for_status()
    print(f"Version {version} of {model_name} transitioned to Production")


def apply_ab_decision():
    """Читает решение A/B теста из S3 и выполняет перевод кандидата в Production."""
    # Читаем decision file из S3 через Yandex Object Storage API (H2O/Python requests
    # проще поверх S3 GET). В Managed Airflow доступ к S3 есть через статические ключи
    # (MLFLOW_AWS_ACCESS_KEY_ID / MLFLOW_AWS_SECRET_ACCESS_KEY).
    import boto3

    bucket = YC_S3_BUCKET
    prefix = AB_DECISION_OUTPUT.split("://", 1)[-1].split("/", 1)
    if len(prefix) == 2:
        folder = prefix[1]
    else:
        folder = "ab_validation"

    s3 = boto3.client(
        "s3",
        endpoint_url="https://storage.yandexcloud.net",
        aws_access_key_id=MLFLOW_AWS_ACCESS_KEY_ID,
        aws_secret_access_key=MLFLOW_AWS_SECRET_ACCESS_KEY,
        region_name="ru-central1",
    )
    # каждый запуск A/B пишет решение в УНИКАЛЬНЫЙ каталог,
# избегая FileAlreadyExistsException; берём самый свежий непустой part-файл.
    candidates = []
    token = None
    while True:
        kwargs = dict(Bucket=bucket, Prefix=f"{folder}/decision_")
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        candidates.extend(resp.get("Contents", []))
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    parts = [c for c in candidates
             if c["Key"].endswith("part-") or "/part-" in c["Key"] and c.get("Size", 0) > 0]
    if not parts:
        raise RuntimeError("Не найден decision файл в S3")
    latest = max(parts, key=lambda c: c.get("LastModified") or "")
    key = latest["Key"]

    obj = s3.get_object(Bucket=bucket, Key=key)
    decision_text = obj["Body"].read().decode("utf-8").strip()
    decision = json.loads(decision_text)
    print(f"AB decision: {decision}")

    should_deploy = decision.get("should_deploy")
    candidate_version = decision.get("candidate_version")
    base = MLFLOW_TRACKING_URI.rstrip("/")

    if should_deploy and candidate_version:
        _transition_to_production(base, MLFLOW_MODEL_NAME, candidate_version)
        print(f"Candidate version {candidate_version} promoted to Production")
    else:
        print("Decision: НЕ выкатываем кандидата (нет значимого улучшения или нет решения).")


with DAG(
    "fraud_ab_validation_pipeline",
    default_args=default_args,
    description="Weekly: clean data, retrain candidate, A/B-validate vs champion, promote",
    schedule="0 5 * * 1",
    start_date=datetime(2026, 8, 1),
    catchup=False,
    tags=["spark", "dataproc", "mlflow", "ab-test", "validation"],
) as dag:

    setup = PythonOperator(
        task_id="setup_connections",
        python_callable=setup_connections,
    )

    create = DataprocCreateClusterOperator(
        task_id="create_cluster",
        folder_id=YC_FOLDER_ID,
        cluster_name=f"fraud-ab-{uuid.uuid4().hex[:8]}",
        cluster_description="Temporary Spark cluster for fraud A/B validation",
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
            # Pin ONE consistent ML stack into the base conda env (Python 3.8).
            # Data Proc image 2.1 ships numpy 1.20.1/pandas 1.2.4/sklearn 0.24.1,
            # which is too old for mlflow 2.16.2. Previously we worked around it
            # by pip-installing a *second* numpy+pandas into /tmp at job start;
            # that created two numpy installs in one process and crashed the A/B
            # bootstrap with "RecursionError: maximum recursion depth exceeded"
            # (base-numpy-1.20 and /tmp-numpy-1.26.4 calling into each other).
            # Explicit exact pins make `pip install <pkg>==<ver>` force-upgrade the
            # existing packages so the whole job runs on a single numpy 1.24.4.
            # All versions are Python-3.8 compatible and mutually consistent.
            "pip:numpy": "1.24.4",
            "pip:pandas": "2.0.3",
            "pip:scikit-learn": "1.3.2",
            "pip:scipy": "1.10.1",
            "pip:mlflow": "2.16.2",
            # MLflow artifact root is s3://mlflow-bucket-ek/artifacts (bare "s3"
            # scheme). Data Proc registers only the "s3a" FileSystem, so
            # PipelineModel.save() on the driver fails with
            # "No FileSystem for scheme 's3'". Alias s3 -> S3A so the driver can
            # write the model directly to the s3:// artifact root.
            "core:fs.s3.impl": "org.apache.hadoop.fs.s3a.S3AFileSystem",
            "core:fs.s3.impl.disable.cache": "true",
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
        # Pin cluster logs to the folder's "default" log group so a failing job
        # always surfaces its real stdout/stderr (job_output/containers) instead
        # of the opaque YC "Unknown error" with no retrievable trace.
        log_group_id=YC_LOG_GROUP_ID,
    )

    submit_clean = DataprocCreatePysparkJobOperator(
        task_id="submit_cleaning_job",
        main_python_file_uri=CLEANING_SCRIPT_URI,
        args=["--input", INPUT_S3, "--output", CLEAN_OUTPUT_S3,
              "--sample-fraction", YC_SAMPLE_FRACTION],
        connection_id=YC_SA_CONN_ID,
        cluster_id="{{ task_instance.xcom_pull(task_ids='create_cluster', key='cluster_id') }}",
    )

    train_args = [
        "--input", CLEAN_OUTPUT_S3,
        "--mlflow-tracking-uri", MLFLOW_TRACKING_URI,
        "--mlflow-s3-endpoint", MLFLOW_S3_ENDPOINT,
        "--experiment-name", MLFLOW_EXPERIMENT,
        "--model-name", MLFLOW_MODEL_NAME,
        "--sample-fraction", "1.0",
        "--metrics-output", f"s3a://{YC_S3_BUCKET}/mlflow_metrics",
    ]
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

    # --- Шаг валидации: A/B тест кандидата vs champion ----------------------
    ab_test_args = [
        "--input", CLEAN_OUTPUT_S3,
        "--mlflow-tracking-uri", MLFLOW_TRACKING_URI,
        "--mlflow-s3-endpoint", MLFLOW_S3_ENDPOINT,
        "--experiment-name", MLFLOW_AB_EXPERIMENT,
        "--train-experiment-name", MLFLOW_EXPERIMENT,
        "--model-name", MLFLOW_MODEL_NAME,
        "--bootstrap-iterations", AB_BOOTSTRAP_ITERATIONS,
        "--alpha", AB_ALPHA,
        "--effect-threshold", AB_EFFECT_THRESHOLD,
        "--sample-fraction", "1.0",
        "--decision-output", AB_DECISION_OUTPUT,
        "--metrics-output", f"s3a://{YC_S3_BUCKET}/ab_metrics",
    ]
    if MLFLOW_AWS_ACCESS_KEY_ID and MLFLOW_AWS_SECRET_ACCESS_KEY:
        ab_test_args += [
            "--aws-access-key-id", MLFLOW_AWS_ACCESS_KEY_ID,
            "--aws-secret-access-key", MLFLOW_AWS_SECRET_ACCESS_KEY,
        ]

    submit_ab_test = DataprocCreatePysparkJobOperator(
        task_id="submit_ab_test_job",
        main_python_file_uri=AB_TEST_SCRIPT_URI,
        args=ab_test_args,
        connection_id=YC_SA_CONN_ID,
        cluster_id="{{ task_instance.xcom_pull(task_ids='create_cluster', key='cluster_id') }}",
    )

    apply_decision = PythonOperator(
        task_id="apply_ab_decision",
        python_callable=apply_ab_decision,
    )

    delete = DataprocDeleteClusterOperator(
        task_id="destroy_cluster",
        trigger_rule=TriggerRule.ALL_DONE,
        cluster_id="{{ task_instance.xcom_pull(task_ids='create_cluster', key='cluster_id') }}",
    )

    setup >> create >> submit_clean >> submit_train >> submit_ab_test >> apply_decision >> delete
