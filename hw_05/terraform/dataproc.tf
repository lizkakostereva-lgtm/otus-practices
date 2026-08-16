# Spark-кластер НЕ создаётся Terraform.
#
# По условию ДЗ постоянный кластер не нужен: на каждый запуск DAG
# (airflow/dags/spark_cleaning_dag.py) создаёт временный кластер Yandex Data Proc,
# запускает PySpark-задание очистки и удаляет кластер.
#
# Операторы Airflow (airflow.providers.yandex):
#   - DataprocCreateClusterOperator    — создание кластера
#   - DataprocCreatePysparkJobOperator — spark-submit скрипта очистки
#   - DataprocDeleteClusterOperator    — удаление кластера
