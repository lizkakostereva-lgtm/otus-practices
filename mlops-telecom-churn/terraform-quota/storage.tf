resource "yandex_iam_service_account" "mlops" {
  name        = "mlops-churn-quota-sa"
  description = "S3 access, image push/pull for the quota stand"
}

resource "yandex_resourcemanager_folder_iam_member" "storage" {
  folder_id = var.folder_id
  role      = "storage.editor"
  member    = "serviceAccount:${yandex_iam_service_account.mlops.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "pusher" {
  folder_id = var.folder_id
  role      = "container-registry.images.pusher"
  member    = "serviceAccount:${yandex_iam_service_account.mlops.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "puller" {
  folder_id = var.folder_id
  role      = "container-registry.images.puller"
  member    = "serviceAccount:${yandex_iam_service_account.mlops.id}"
}

resource "yandex_iam_service_account_static_access_key" "s3" {
  service_account_id = yandex_iam_service_account.mlops.id
  description        = "Static key for Object Storage (datasets + MLflow artifacts)"
}

resource "yandex_iam_service_account_key" "ci" {
  service_account_id = yandex_iam_service_account.mlops.id
  description        = "Authorized key: docker login cr.yandex, GitHub Actions"
}

resource "yandex_container_registry" "main" {
  name      = "mlops-churn-quota-registry"
  folder_id = var.folder_id
}

resource "yandex_storage_bucket" "data" {
  bucket     = var.bucket_data_name
  folder_id  = var.folder_id
  access_key = yandex_iam_service_account_static_access_key.s3.access_key
  secret_key = yandex_iam_service_account_static_access_key.s3.secret_key

  force_destroy = true
}

resource "yandex_storage_bucket" "dags" {
  bucket     = var.bucket_dags_name
  folder_id  = var.folder_id
  access_key = yandex_iam_service_account_static_access_key.s3.access_key
  secret_key = yandex_iam_service_account_static_access_key.s3.secret_key

  force_destroy = true
}

resource "yandex_storage_bucket" "mlflow" {
  bucket     = var.bucket_mlflow_name
  folder_id  = var.folder_id
  access_key = yandex_iam_service_account_static_access_key.s3.access_key
  secret_key = yandex_iam_service_account_static_access_key.s3.secret_key

  force_destroy = true
}
