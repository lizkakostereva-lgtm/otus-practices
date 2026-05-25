output "bucket_url" {
  value = "https://storage.yandexcloud.net/${yandex_storage_bucket.spark_bucket.bucket}"
}

output "cluster_name" {
  value = yandex_dataproc_cluster.spark_cluster.name
}
