output "bucket_name" {
  value = yandex_storage_bucket.spark_bucket.bucket
}

output "service_account_id" {
  description = "ID dataproc-sa (в Airflow variable DP_SA_ID)"
  value       = yandex_iam_service_account.dataproc_sa.id
}

output "security_group_id" {
  description = "ID security group Data Proc (в Airflow variable DP_SECURITY_GROUP_ID)"
  value       = yandex_vpc_security_group.spark_sg.id
}

output "airflow_webui_url" {
  value = "https://${yandex_airflow_cluster.airflow.id}.airflow.yandexcloud.net"
}

output "airflow_dags_bucket" {
  value = yandex_storage_bucket.airflow_dags_bucket.bucket
}

output "airflow_service_account_id" {
  value = yandex_iam_service_account.airflow_sa.id
}

output "nat_rt_id" {
  value = yandex_vpc_route_table.nat_rt.id
}

output "airflow_sa_auth_key_public" {
  value = yandex_iam_service_account_key.airflow_sa_key.public_key
}

output "airflow_sa_auth_key_private" {
  value     = yandex_iam_service_account_key.airflow_sa_key.private_key
  sensitive = true
}

output "airflow_sa_auth_key_id" {
  value = yandex_iam_service_account_key.airflow_sa_key.id
}

# --- MLflow ---
output "mlflow_vm_public_ip" {
  value = yandex_compute_instance.mlflow_vm.network_interface.0.nat_ip_address
}

output "mlflow_vm_internal_ip" {
  value = yandex_compute_instance.mlflow_vm.network_interface.0.ip_address
}

output "mlflow_tracking_uri" {
  description = "URI MLflow Tracking для Airflow variable MLFLOW_TRACKING_URI"
  value       = "http://${yandex_compute_instance.mlflow_vm.network_interface.0.ip_address}:5000"
}

output "mlflow_artifact_root" {
  value = "s3://${var.mlflow_bucket_name}/artifacts"
}

output "mlflow_sa_access_key_id" {
  value = yandex_iam_service_account_static_access_key.mlflow_sa_key.access_key
}

output "mlflow_sa_secret_key" {
  value     = yandex_iam_service_account_static_access_key.mlflow_sa_key.secret_key
  sensitive = true
}

# --- PostgreSQL VM ---
output "postgres_vm_public_ip" {
  value = yandex_compute_instance.postgres_vm.network_interface.0.nat_ip_address
}

output "postgres_vm_internal_ip" {
  description = "Internal IP postgres-vm (для ansible variable pg_host)"
  value       = yandex_compute_instance.postgres_vm.network_interface.0.ip_address
}

# --- Managed Kafka ---
output "kafka_cluster_id" {
  description = "ID кластера Managed Kafka (для Airflow variables)"
  value       = yandex_mdb_kafka_cluster.fraud_kafka.id
}

output "kafka_bootstrap_servers" {
  description = "Bootstrap-серверы Kafka (SASL_SSL, порт 9091) для Airflow variable KAFKA_BOOTSTRAP_SERVERS"
  value       = join(",", [for h in yandex_mdb_kafka_cluster.fraud_kafka.host : "${h.name}:9091"])
}

output "kafka_bootstrap_plain" {
  description = "Bootstrap-серверы Kafka (SASL_PLAINTEXT, порт 9092) для тестов с localhost"
  value       = join(",", [for h in yandex_mdb_kafka_cluster.fraud_kafka.host : "${h.name}:9092"])
}

output "kafka_input_topic" {
  value = yandex_mdb_kafka_topic.input_topic.name
}

output "kafka_output_topic" {
  value = yandex_mdb_kafka_topic.output_topic.name
}