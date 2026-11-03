"""Full-variant retraining DAG: Airflow raises a temporary Spark cluster.

The quota stand cannot fit Yandex Data Processing, so the test pipeline runs
the sklearn steps inline (airflow/dags/churn_retrain_dag.py). This DAG is the
full-variant equivalent: on every run Airflow
    1. creates a throwaway Data Proc (Spark) cluster,
    2. submits spark/preprocess_churn.py  (raw CSV -> cleaned Parquet on S3),
    3. submits spark/train_churn.py       (Spark ML pipeline -> MLflow),
    4. promotes the candidate in the MLflow Model Registry,
    5. deletes the cluster (always, so nothing idles between runs).

Configuration is read from Airflow Variables (Admin -> Variables); the values
come from `terraform -chdir=terraform output`:
    YC_FOLDER_ID, YC_SUBNET_ID, YC_S3_BUCKET (data), YC_SSH_PUBLIC_KEY, YC_ZONE
    DP_SA_ID, DP_SA_JSON, DP_SECURITY_GROUP_ID   (terraform/dataproc.tf)
    MLFLOW_TRACKING_URI, MLFLOW_AWS_ACCESS_KEY_ID, MLFLOW_AWS_SECRET_ACCESS_KEY
The DAG also needs an Airflow connection of type "yandexcloud" (id: yc-dataproc)
with the authorized key JSON in Extra (see README, full-variant section).
"""

import os
from datetime import datetime, timedelta

from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator
from airflow.providers.yandex.operators.dataproc import (
    DataprocCreateClusterOperator,
    DataprocCreatePysparkJobOperator,
    DataprocDeleteClusterOperator,
)
from airflow.utils.trigger_rule import TriggerRule

from airflow import DAG

DATA_BUCKET = os.environ.get("S3_DATA_BUCKET", "")
S3_ENDPOINT = "https://storage.yandexcloud.net"

YC_CONN_ID = "yc-dataproc"

S3_RAW = (
    f"s3a://{DATA_BUCKET}/raw/WA_Fn-UseC_-Telco-Customer-Churn.csv"
    if DATA_BUCKET else "s3a://<bucket_data>/raw/WA_Fn-UseC_-Telco-Customer-Churn.csv"
)
S3_CLEAN = f"s3a://{DATA_BUCKET}/processed/cleaned.parquet" if DATA_BUCKET else "s3a://<bucket_data>/processed/cleaned.parquet"
PREPROCESS_URI = f"s3a://{DATA_BUCKET}/spark/preprocess_churn.py"
TRAIN_URI = f"s3a://{DATA_BUCKET}/spark/train_churn.py"

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://10.150.10.10:5000")
AWS_ACCESS_KEY_ID = os.environ.get("MLFLOW_AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("MLFLOW_AWS_SECRET_ACCESS_KEY", "")

# retries=0: destroy_cluster (ALL_DONE) removes the cluster after any failure,
# so retrying a submit job without the cluster is doomed.
default_args = {
    "owner": "mlops",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 0,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    "churn_spark_retrain",
    default_args=default_args,
    description="Full variant: temporary Dataproc Spark cluster per retrain",
    schedule_interval="0 3 * * 0",  # weekly Sunday 03:00 (same as k8s CronJob)
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["mlops", "churn", "spark", "dataproc"],
) as dag:

    start = EmptyOperator(task_id="start")

    create_cluster = DataprocCreateClusterOperator(
        task_id="create_spark_cluster",
        folder_id=os.environ.get("YC_FOLDER_ID", ""),
        cluster_name="churn-retrain-{{ ts_nodash }}",
        cluster_description="Temporary Spark cluster for churn retraining",
        subnet_id=os.environ.get("YC_SUBNET_ID", ""),
        s3_bucket=os.environ.get("YC_S3_BUCKET", ""),
        service_account_id=os.environ.get("DP_SA_ID", ""),
        ssh_public_keys=os.environ.get("YC_SSH_PUBLIC_KEY", ""),
        zone=os.environ.get("YC_ZONE", "ru-central1-d"),
        cluster_image_version="2.1",
        services=["SPARK", "HDFS", "YARN"],
        security_group_ids=[os.environ.get("DP_SECURITY_GROUP_ID", "")],
        properties={
            "spark:spark.driver.memory": "2g",
            "spark:spark.driver.memoryOverhead": "1g",
            # Match the Tracking Server version, otherwise mlflow.spark fails
            # on the nodes (course lesson from hw_06).
            "pip:mlflow": "2.16.2",
        },
        masternode_resource_preset="s3-c2-m8",
        masternode_disk_type="network-hdd",
        masternode_disk_size=40,
        datanode_resource_preset="s3-c2-m8",
        datanode_disk_type="network-hdd",
        datanode_disk_size=40,
        datanode_count=1,
        computenode_count=0,
        connection_id=YC_CONN_ID,
    )

    prepare_stage = "{{ task_instance.xcom_pull(task_ids='create_spark_cluster', key='cluster_id') }}"

    spark_preprocess = DataprocCreatePysparkJobOperator(
        task_id="spark_preprocess",
        main_python_file_uri=PREPROCESS_URI,
        args=["--input", S3_RAW, "--output", S3_CLEAN],
        connection_id=YC_CONN_ID,
        cluster_id=prepare_stage,
    )

    train_args = [
        "--input", S3_CLEAN,
        "--mlflow-tracking-uri", MLFLOW_TRACKING_URI,
        "--experiment-name", "telecom-churn",
        "--model-name", "telecom-churn-model",
    ]
    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        train_args += [
            "--aws-access-key-id", AWS_ACCESS_KEY_ID,
            "--aws-secret-access-key", AWS_SECRET_ACCESS_KEY,
        ]

    spark_train = DataprocCreatePysparkJobOperator(
        task_id="spark_train",
        main_python_file_uri=TRAIN_URI,
        args=train_args,
        connection_id=YC_CONN_ID,
        cluster_id=prepare_stage,
    )

    promote = BashOperator(
        task_id="promote_model",
        # Same threshold logic as everywhere else: scripts/promote_model.py
        # against the MLflow Model Registry (params.yaml -> promote).
        bash_command="cd /opt/airflow/dags/repo && python scripts/promote_model.py",
        env={
            "MLFLOW_TRACKING_URI": MLFLOW_TRACKING_URI,
            "S3_ENDPOINT_URL": S3_ENDPOINT,
        },
    )

    delete_cluster = DataprocDeleteClusterOperator(
        task_id="delete_spark_cluster",
        trigger_rule=TriggerRule.ALL_DONE,
        cluster_id=prepare_stage,
        connection_id=YC_CONN_ID,
    )

    end = EmptyOperator(task_id="end", trigger_rule=TriggerRule.ALL_DONE)

    start >> create_cluster >> spark_preprocess >> spark_train >> promote
    [promote, create_cluster] >> delete_cluster >> end