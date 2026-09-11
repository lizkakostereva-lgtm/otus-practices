"""DAG: online fraud inference + load test on Kafka (hw_08).

Конвейер (один временный Data Proc-кластер на запуск):
  1. setup             - проверить обязательные переменные Airflow
  2. create_cluster    - создать временный Spark-кластер (Yandex Data Proc)
  3. start_producer    - (параллельно) kafka_producer.py: переиграть исторические
                         транзакции в Kafka topic "fraud-transactions" с целевой TPS
  4. start_streaming   - (параллельно) fraud_streaming_inference.py: Structured
                         Streaming читает topic, инференсит champion-модель из MLflow
                         и пишет предсказания в topic "fraud-predictions"
  5. measure_perf      - PythonOperator: сверить producer/consumer метрики из S3 и
                         оценить, при каком TPS consumer начинает отставать (лаги)
  6. destroy_cluster   - всегда удалить кластер (trigger_rule=ALL_DONE)

Kafka (Managed Kafka, создаётся Terraform-ом один раз) живёт постоянно; удаляется
только при make destroy. Временный Data Proc-кластер создаётся и удаляется DAG'ом
в каждом запуске, чтобы не платить в простое.

Замер "точки перегиба": задаём TPS в Airflow variable STREAMING_TPS и запускаем
run. Производитель гонит фиксированное число сообщений (STREAMING_MESSAGES) с этой
скоростью; консьюмер работает STREAMING_DURATION_SECONDS секунд. В summary метриках
fraud_streaming (эксперимент MLflow) и CSV consumer_*.csv видно consumed vs
produced и среднее время обработки батча — при росте TPS consumer перестаёт
успевать (processed < input, avg_batch_ms растёт = появляется лаг).
"""

import json
import uuid
from datetime import datetime

from airflow import DAG
from airflow.models import Variable
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator
from airflow.providers.yandex.operators.dataproc import (
    DataprocCreateClusterOperator,
    DataprocCreatePysparkJobOperator,
    DataprocDeleteClusterOperator,
)
from airflow.utils.trigger_rule import TriggerRule

# --- Переменные Airflow (см. README) ----------------------------------------
YC_FOLDER_ID = Variable.get("YC_FOLDER_ID", default_var=None)
YC_ZONE = Variable.get("YC_ZONE", default_var="ru-central1-a")
YC_SUBNET_ID = Variable.get("YC_SUBNET_ID", default_var=None)
YC_S3_BUCKET = Variable.get("YC_S3_BUCKET", default_var="spark-bucket-ek")
YC_SSH_PUBLIC_KEY = Variable.get("YC_SSH_PUBLIC_KEY", default_var=None)
DP_SA_ID = Variable.get("DP_SA_ID", default_var=None)
DP_SECURITY_GROUP_ID = Variable.get("DP_SECURITY_GROUP_ID", default_var=None)

_DP_SA_JSON = Variable.get("DP_SA_JSON", default_var=None)
DP_SA_JSON = json.dumps(_DP_SA_JSON) if isinstance(_DP_SA_JSON, dict) else _DP_SA_JSON

# --- Kafka -------------------------------------------------------------------
KAFKA_BOOTSTRAP = Variable.get("KAFKA_BOOTSTRAP_SERVERS", default_var=None)
KAFKA_INPUT_TOPIC = Variable.get("KAFKA_INPUT_TOPIC", default_var="fraud-transactions")
KAFKA_OUTPUT_TOPIC = Variable.get("KAFKA_OUTPUT_TOPIC", default_var="fraud-predictions")
KAFKA_USER = Variable.get("KAFKA_USER", default_var="fraud-user")
KAFKA_PASSWORD = Variable.get("KAFKA_PASSWORD", default_var=None)

# --- Параметры нагрузочного теста -------------------------------------------
INPUT_S3 = Variable.get("YC_INPUT_S3", default_var="s3a://otus-mlops-source-data/*.txt")
CLEAN_OUTPUT_S3 = Variable.get("YC_CLEAN_OUTPUT_S3",
                               default_var=f"s3a://{YC_S3_BUCKET}/fraud_clean_parquet")
KAFKA_PRODUCER_SCRIPT = f"s3a://{YC_S3_BUCKET}/scripts/kafka_producer.py"
KAFKA_STREAM_SCRIPT = f"s3a://{YC_S3_BUCKET}/scripts/fraud_streaming_inference.py"
STREAMING_TPS = Variable.get("STREAMING_TPS", default_var="50")
STREAMING_MESSAGES = Variable.get("STREAMING_MESSAGES", default_var="100000")
STREAMING_DURATION = Variable.get("STREAMING_DURATION_SECONDS", default_var="300")
SAMPLE_FRACTION = Variable.get("YC_SAMPLE_FRACTION", default_var="0.05")
METRICS_S3 = Variable.get("KAFKA_METRICS_S3",
                          default_var=f"s3a://{YC_S3_BUCKET}/kafka_test_metrics")

# --- MLflow ------------------------------------------------------------------
MLFLOW_TRACKING_URI = Variable.get("MLFLOW_TRACKING_URI", default_var=None)
MLFLOW_S3_ENDPOINT = Variable.get("MLFLOW_S3_ENDPOINT_URL",
                                  default_var="https://storage.yandexcloud.net")
MLFLOW_AWS_ACCESS_KEY_ID = Variable.get("MLFLOW_AWS_ACCESS_KEY_ID", default_var=None)
MLFLOW_AWS_SECRET_ACCESS_KEY = Variable.get("MLFLOW_AWS_SECRET_ACCESS_KEY", default_var=None)
MLFLOW_MODEL_NAME = Variable.get("MLFLOW_MODEL_NAME", default_var="fraud-detector")
STREAM_EXPERIMENT = Variable.get("KAFKA_STREAM_EXPERIMENT", default_var="fraud_streaming")

# --- Общие параметры DAG -----------------------------------------------------
default_args = {
    "owner": "student",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 0,
}

YC_SA_CONN_ID = "yc-dataproc"


def setup_variables():
    """Проверяет обязательные переменные Airflow (без них нет смысла запускать)."""
    required = {
        "YC_FOLDER_ID": YC_FOLDER_ID,
        "YC_SUBNET_ID": YC_SUBNET_ID,
        "YC_SSH_PUBLIC_KEY": YC_SSH_PUBLIC_KEY,
        "DP_SA_ID": DP_SA_ID,
        "DP_SA_JSON": DP_SA_JSON,
        "DP_SECURITY_GROUP_ID": DP_SECURITY_GROUP_ID,
        "MLFLOW_TRACKING_URI": MLFLOW_TRACKING_URI,
        "KAFKA_BOOTSTRAP_SERVERS": KAFKA_BOOTSTRAP,
        "KAFKA_PASSWORD": KAFKA_PASSWORD,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(
            "Не заданы обязательные переменные Airflow: " + ", ".join(missing)
            + ". Задайте их в Admin -> Variables, см. README."
        )


def measure_performance():
    """Сравнивает метрики producer/consumer из S3 и печатает сводку замеров."""
    import boto3

    bucket = YC_S3_BUCKET
    folder = METRICS_S3.split("://", 1)[-1].split("/", 1)[-1]

    s3 = boto3.client(
        "s3",
        endpoint_url="https://storage.yandexcloud.net",
        aws_access_key_id=MLFLOW_AWS_ACCESS_KEY_ID,
        aws_secret_access_key=MLFLOW_AWS_SECRET_ACCESS_KEY,
        region_name="ru-central1",
    )

    def _read_csv(prefix):
        token = None
        keys = []
        while True:
            kwargs = dict(Bucket=bucket, Prefix=f"{folder}/{prefix}")
            if token:
                kwargs["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kwargs)
            keys.extend(resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        parts = [(k["Key"]) for k in keys if k.get("Size", 0) > 0]
        if not parts:
            return None
        latest = max(parts)
        obj = s3.get_object(Bucket=bucket, Key=latest)
        text = obj["Body"].read().decode("utf-8")
        d = {}
        for line in text.strip().splitlines()[1:]:  # пропускаем header
            if "," in line:
                k, _, v = line.partition(",")
                d[k.strip()] = v.strip()
        return d

    prod = _read_csv("producer_")
    cons = _read_csv("consumer_")
    print("=== Producer metrics ===")
    print(json.dumps(prod, indent=2))
    print("=== Consumer metrics ===")
    print(json.dumps(cons, indent=2))

    if prod and cons:
        try:
            produced = int(prod.get("produced", 0))
            consumed = int(cons.get("consumed", 0))
            target = int(prod.get("tps_target", 0))
            lag = max(0, produced - consumed)
            print(f"SUMMARY target_tps={target} produced={produced} consumed={consumed} lag={lag}")
            if lag > 0:
                print(f"TIPPING POINT: consumer отстал на {lag} сообщений — "
                      f"на TPS {target} кластер не успевает за продюсером.")
            else:
                print(f"OK: при TPS {target} consumer успевает (lag=0).")
        except (TypeError, ValueError) as e:
            print(f"Не удалось распарсить метрики производителя: {e}")

    # Отдельно — качество модели, которое стример оценил на потоковых
    # предсказаниях (вариант A: метка tx_fraud в сообщении Kafka).
    if cons and cons.get("accuracy"):
        try:
            print(f"QUALITY acc={cons.get('accuracy')} prec={cons.get('precision')} "
                  f"rec={cons.get('recall')} f1={cons.get('f1')} "
                  f"on n_labels={cons.get('n_labels')} "
                  f"(tp/cm: {cons.get('tp')}/{cons.get('fp')}/{cons.get('tn')}/{cons.get('fn')})")
        except TypeError:
            print("Не удалось распарсить метрики качества из consumer CSV.")


with DAG(
    "fraud_streaming_pipeline",
    default_args=default_args,
    description="Online fraud inference + Kafka load test (tipping point for consumer lag)",
    schedule=None,
    start_date=datetime(2026, 9, 1),
    catchup=False,
    tags=["spark", "dataproc", "kafka", "streaming", "mlflow", "fraud"],
) as dag:

    setup = PythonOperator(
        task_id="setup_variables",
        python_callable=setup_variables,
    )

    create = DataprocCreateClusterOperator(
        task_id="create_cluster",
        folder_id=YC_FOLDER_ID,
        cluster_name=f"fraud-stream-{uuid.uuid4().hex[:8]}",
        cluster_description="Temporary Spark cluster for Kafka streaming inference",
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
            # Стабильный ML-стек в базовом conda env (Python 3.8) - как в hw_07.
            "pip:numpy": "1.24.4",
            "pip:pandas": "2.0.3",
            "pip:scikit-learn": "1.3.2",
            "pip:scipy": "1.10.1",
            "pip:mlflow": "2.16.2",
            # Producer шлёт сообщения из драйвера через confluent-kafka.
            "pip:confluent-kafka": "2.5.0",
            # Alias s3 -> s3a, чтобы драйвер писал модель сразу в s3:// artifact root.
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
    )

    # run_id генерируется в каждом скрипте самостоятельно (run-<epoch_ms>),
    # т.к. аргумент args оператора Data Proc не шаблонизируется Jinja (в отличие
    # от cluster_id). Producer и consumer пишут CSV с одним run_id; DAG в
    # measure_performance берёт последний producer_*.csv и consumer_*.csv.
    # Checkpoint для стримера должен быть уникален на запуск — поэтому DAG
    # передаёт только БАЗОВЫЙ каталог, а скрипт сам дописывает туда run_id.

    producer_args = [
        "--input", CLEAN_OUTPUT_S3,
        "--bootstrap-servers", KAFKA_BOOTSTRAP,
        "--topic", KAFKA_INPUT_TOPIC,
        "--user", KAFKA_USER,
        "--password", KAFKA_PASSWORD,
        "--tps", STREAMING_TPS,
        "--max-messages", STREAMING_MESSAGES,
        "--sample-fraction", SAMPLE_FRACTION,
        "--metrics-output", METRICS_S3,
    ]

    start_producer = DataprocCreatePysparkJobOperator(
        task_id="start_producer",
        main_python_file_uri=KAFKA_PRODUCER_SCRIPT,
        args=producer_args,
        connection_id=YC_SA_CONN_ID,
        cluster_id="{{ task_instance.xcom_pull(task_ids='create_cluster', key='cluster_id') }}",
    )

    stream_args = [
        "--bootstrap-servers", KAFKA_BOOTSTRAP,
        "--input-topic", KAFKA_INPUT_TOPIC,
        "--output-topic", KAFKA_OUTPUT_TOPIC,
        "--user", KAFKA_USER,
        "--password", KAFKA_PASSWORD,
        "--model-uri", f"models:/{MLFLOW_MODEL_NAME}/Production",
        "--mlflow-tracking-uri", MLFLOW_TRACKING_URI,
        "--mlflow-s3-endpoint", MLFLOW_S3_ENDPOINT,
        "--checkpoint-location", f"s3a://{YC_S3_BUCKET}/stream_checkpoint",
        "--duration-seconds", STREAMING_DURATION,
        "--metrics-output", METRICS_S3,
        "--experiment-name", STREAM_EXPERIMENT,
    ]
    if MLFLOW_AWS_ACCESS_KEY_ID and MLFLOW_AWS_SECRET_ACCESS_KEY:
        stream_args += [
            "--aws-access-key-id", MLFLOW_AWS_ACCESS_KEY_ID,
            "--aws-secret-access-key", MLFLOW_AWS_SECRET_ACCESS_KEY,
        ]

    start_streaming = DataprocCreatePysparkJobOperator(
        task_id="start_streaming",
        main_python_file_uri=KAFKA_STREAM_SCRIPT,
        args=stream_args,
        connection_id=YC_SA_CONN_ID,
        cluster_id="{{ task_instance.xcom_pull(task_ids='create_cluster', key='cluster_id') }}",
    )

    measure = PythonOperator(
        task_id="measure_performance",
        python_callable=measure_performance,
    )

    join = EmptyOperator(task_id="join_producer_stream")

    delete = DataprocDeleteClusterOperator(
        task_id="destroy_cluster",
        trigger_rule=TriggerRule.ALL_DONE,
        cluster_id="{{ task_instance.xcom_pull(task_ids='create_cluster', key='cluster_id') }}",
    )

    setup >> create
    create >> start_producer >> join
    create >> start_streaming >> join
    join >> measure >> delete