"""Periodic retraining of the URL fraud model, with metrics in MLflow.

This DAG is the *external* piece of the assignment: it lives in the project's
git repository and is delivered to the in-cluster Airflow by git-sync
(see airflow/values.yaml -> dags.gitSync). Editing the DAG = pushing to git;
no image rebuild, no `kubectl cp`.

Pipeline
    start
      -> build_training_image_note?   (image is built by CI, see docs)
      -> retrain            (KubernetesPodOperator: mlops/retrain.py)
      -> promote            (only if RETRAIN_PROMOTE is true) — moves the new
                            model version to alias `champion` in MLflow
      -> end

The retrain pod does the heavy lifting (sklearn fit + MLflow logging) using the
same `app.train` code the API serves, and fails the task itself if F1/AUC
regress below the configured floor — so Airflow never promotes a bad model.
"""

from __future__ import annotations

import os

import pendulum
from airflow.models import Variable
from airflow.operators.empty import EmptyOperator
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from kubernetes.client import models as k8s

from airflow import DAG

# ---- configuration (Airflow Variables, with env-var fallbacks) --------------
# Admin -> Variables, or `airflow variables set url_fraud_retrain_image <ref>`.
RETRAIN_IMAGE = Variable.get(
    "url_fraud_retrain_image",
    default_var=os.getenv(
        "RETRAIN_IMAGE", "cr.yandex/REGISTRY_ID/url-fraud-retrain:latest"
    ),
)
NAMESPACE = Variable.get("url_fraud_namespace", default_var="url-fraud")
MLFLOW_TRACKING_URI = Variable.get(
    "mlflow_tracking_uri",
    default_var="http://mlflow.url-fraud.svc.cluster.local:5000",
)
MLFLOW_EXPERIMENT = Variable.get("mlflow_experiment", default_var="fraud_url_detection")
MLFLOW_MODEL_NAME = Variable.get("mlflow_model_name", default_var="url-fraud-detector")
RETRAIN_DATA_URL = Variable.get(
    "retrain_data_url",
    default_var=(
        "https://raw.githubusercontent.com/faizann24/"
        "Using-machine-learning-to-detect-malicious-URLs/master/data/data.csv"
    ),
)
RETRAIN_PROMOTE = Variable.get("retrain_promote", default_var="true").lower() == "true"
MIN_F1 = Variable.get("retrain_min_f1", default_var="0.70")

# Secrets are injected as an Airflow Connection / Secret backend in production;
# for the homework these come from a k8s Secret mounted as env in the pod.
S3_ENV = [
    k8s.V1EnvVar(
        name="AWS_ACCESS_KEY_ID",
        value_from=k8s.V1EnvVarSource(
            secret_key_ref=k8s.V1SecretKeySelector(
                name="mlflow-secrets", key="AWS_ACCESS_KEY_ID", optional=True
            )
        ),
    ),
    k8s.V1EnvVar(
        name="AWS_SECRET_ACCESS_KEY",
        value_from=k8s.V1EnvVarSource(
            secret_key_ref=k8s.V1SecretKeySelector(
                name="mlflow-secrets", key="AWS_SECRET_ACCESS_KEY", optional=True
            )
        ),
    ),
    k8s.V1EnvVar(name="AWS_ENDPOINT_URL", value="https://storage.yandexcloud.net"),
    k8s.V1EnvVar(name="MLFLOW_S3_ENDPOINT_URL", value="https://storage.yandexcloud.net"),
    k8s.V1EnvVar(name="AWS_DEFAULT_REGION", value="ru-central1"),
]

COMMON_ENV = [
    k8s.V1EnvVar(name="MLFLOW_TRACKING_URI", value=MLFLOW_TRACKING_URI),
    k8s.V1EnvVar(name="MLFLOW_EXPERIMENT", value=MLFLOW_EXPERIMENT),
    k8s.V1EnvVar(name="MLFLOW_MODEL_NAME", value=MLFLOW_MODEL_NAME),
    *S3_ENV,
]

# Pods are tiny and short-lived; keep them on the same node pool as the API.
POD_RESOURCES = k8s.V1ResourceRequirements(
    requests={"cpu": "200m", "memory": "512Mi"},
    limits={"memory": "1Gi"},
)

default_args = {
    "owner": "ml-team",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": pendulum.duration(minutes=3),
}

with DAG(
    dag_id="url_fraud_retrain",
    description="Periodic retraining of the URL fraud model with MLflow tracking",
    default_args=default_args,
    start_date=pendulum.datetime(2026, 10, 1, tz="UTC"),
    schedule="0 3 * * *",  # every night at 03:00 UTC
    catchup=False,
    max_active_runs=1,
    tags=["url-fraud", "ml", "retrain", "hw_10"],
    doc_md=__doc__,
) as dag:
    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    retrain = KubernetesPodOperator(
        task_id="retrain",
        name="url-fraud-retrain",
        namespace=NAMESPACE,
        image=RETRAIN_IMAGE,
        image_pull_policy="IfNotPresent",
        service_account_name="airflow-worker",  # reuses the chart's worker SA
        cmds=["python", "-m", "mlops.retrain"],
        arguments=[
            "--tracking-uri",
            MLFLOW_TRACKING_URI,
            "--experiment",
            MLFLOW_EXPERIMENT,
            "--model-name",
            MLFLOW_MODEL_NAME,
            "--data",
            RETRAIN_DATA_URL,
            "--sample-frac",
            "0.1",
            "--min-f1",
            str(MIN_F1),
            "--register",
        ],
        env_vars=COMMON_ENV,
        container_resources=POD_RESOURCES,
        get_logs=True,
        log_events_on_failure=True,
        on_finish_action="delete_pod",
        # Keep the trained artifact on the cluster until the promote task runs.
        is_delete_operator_pod=True,
        labels={"app.kubernetes.io/part-of": "url-fraud", "airflow-task": "retrain"},
    )

    promote = KubernetesPodOperator(
        task_id="promote",
        name="url-fraud-promote",
        namespace=NAMESPACE,
        image=RETRAIN_IMAGE,
        image_pull_policy="IfNotPresent",
        service_account_name="airflow-worker",
        cmds=["python", "-m", "mlops.promote"],
        arguments=[
            "--tracking-uri",
            MLFLOW_TRACKING_URI,
            "--model-name",
            MLFLOW_MODEL_NAME,
            "--alias",
            "champion",
        ],
        env_vars=COMMON_ENV,
        container_resources=POD_RESOURCES,
        get_logs=True,
        on_finish_action="delete_pod",
        is_delete_operator_pod=True,
        trigger_rule="all_success" if RETRAIN_PROMOTE else "none_failed",
        labels={"app.kubernetes.io/part-of": "url-fraud", "airflow-task": "promote"},
    )

    start >> retrain
    if RETRAIN_PROMOTE:
        retrain >> promote >> end
    else:
        retrain >> end
