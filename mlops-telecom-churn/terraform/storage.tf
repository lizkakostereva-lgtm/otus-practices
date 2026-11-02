resource "yandex_storage_bucket" "data" {
  bucket     = var.bucket_data_name
  folder_id  = var.folder_id
  access_key = yandex_iam_service_account_static_access_key.mlflow_s3.access_key
  secret_key = yandex_iam_service_account_static_access_key.mlflow_s3.secret_key

  force_destroy = true
}

resource "yandex_storage_bucket" "dags" {
  bucket     = var.bucket_dags_name
  folder_id  = var.folder_id
  access_key = yandex_iam_service_account_static_access_key.mlflow_s3.access_key
  secret_key = yandex_iam_service_account_static_access_key.mlflow_s3.secret_key

  force_destroy = true
}

resource "yandex_storage_bucket" "mlflow" {
  bucket     = var.bucket_mlflow_name
  folder_id  = var.folder_id
  access_key = yandex_iam_service_account_static_access_key.mlflow_s3.access_key
  secret_key = yandex_iam_service_account_static_access_key.mlflow_s3.secret_key

  force_destroy = true
}
