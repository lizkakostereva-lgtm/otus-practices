output "bucket_name" {
  value = yandex_storage_bucket.spark_bucket.bucket
}

output "bucket_url" {
  value = "https://storage.yandexcloud.net/${yandex_storage_bucket.spark_bucket.bucket}"
}

output "service_account_id" {
  value = yandex_iam_service_account.dataproc_sa.id
}

output "security_group_id" {
  value = yandex_vpc_security_group.spark_sg.id
}

output "cluster_name" {
  value = yandex_dataproc_cluster.spark_cluster.name
}