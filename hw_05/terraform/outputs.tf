output "bucket_name" {
  value = yandex_storage_bucket.spark_bucket.bucket
}

output "bucket_url" {
  value = "https://storage.yandexcloud.net/${yandex_storage_bucket.spark_bucket.bucket}"
}

output "service_account_id" {
  description = "ID сервисного аккаунта dataproc-sa (передать в переменную Airflow DP_SA_ID)"
  value       = yandex_iam_service_account.dataproc_sa.id
}

output "security_group_id" {
  description = "ID security group для Data Proc (передать в переменную Airflow DP_SECURITY_GROUP_ID)"
  value       = yandex_vpc_security_group.spark_sg.id
}

output "airflow_cluster_name" {
  value = yandex_airflow_cluster.airflow.name
}

output "airflow_dags_bucket" {
  value = yandex_storage_bucket.airflow_dags_bucket.bucket
}

output "airflow_service_account_id" {
  value = yandex_iam_service_account.airflow_sa.id
}

output "airflow_webui_url" {
  value = "https://${yandex_airflow_cluster.airflow.id}.airflow.yandexcloud.net"
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
