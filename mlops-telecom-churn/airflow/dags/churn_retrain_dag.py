from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator

default_args = {
    'owner': 'mlops',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    'churn_retrain_pipeline',
    default_args=default_args,
    description='MLOps Telecom Churn: preprocess -> train -> evaluate -> promote',
    schedule_interval='0 3 * * 0',  # weekly Sunday 03:00
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['mlops', 'churn', 'retrain'],
) as dag:

    preprocess = BashOperator(
        task_id='preprocess_data',
        bash_command='cd /opt/airflow/dags/repo && python -m src.data.preprocess',
    )

    train = BashOperator(
        task_id='train_model',
        bash_command='cd /opt/airflow/dags/repo && python -m src.train.train',
    )

    evaluate = BashOperator(
        task_id='evaluate_model',
        bash_command='cd /opt/airflow/dags/repo && python -m src.evaluate.evaluate',
    )

    promote = BashOperator(
        task_id='promote_model',
        bash_command='cd /opt/airflow/dags/repo && python scripts/promote_model.py',
    )

    preprocess >> train >> evaluate >> promote
