resource "yandex_storage_bucket" "spark_bucket" {
  bucket    = var.bucket_name
  folder_id = var.folder_id

  force_destroy = true
}

resource "yandex_storage_bucket" "airflow_dags_bucket" {
  bucket    = var.dags_bucket_name
  folder_id = var.folder_id

  force_destroy = true
}

resource "yandex_storage_bucket" "airflow_logs_bucket" {
  bucket    = "${var.dags_bucket_name}-logs"
  folder_id = var.folder_id

  force_destroy = true
}

resource "yandex_storage_bucket" "mlflow_bucket" {
  bucket    = var.mlflow_bucket_name
  folder_id = var.folder_id

  force_destroy = true
}
