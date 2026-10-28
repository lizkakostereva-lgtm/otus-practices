# Object Storage buckets.
#
#   * mlflow-artifacts-*  - MLflow artifact store (--artifacts-destination s3://<name>/)
#   * url-fraud-kafka-*   - optional export of the attack events / consumer offsets
#
# Access is granted to the dedicated `url-fraud-mlflow-sa` (service_account.tf);
# its static access key is what the in-cluster MLflow secret carries.

resource "yandex_storage_bucket" "mlflow_artifacts" {
  bucket    = var.mlflow_bucket_name
  folder_id = var.folder_id

  # Allow `terraform destroy` to empty the bucket instead of erroring on
  # non-empty content.
  force_destroy = true
}

resource "yandex_storage_bucket" "kafka_data" {
  bucket    = var.kafka_bucket_name
  folder_id = var.folder_id

  force_destroy = true
}

# Keep the artifact bucket private; only the MLflow SA (via its key) touches it.
resource "yandex_storage_bucket_iam_binding" "mlflow_artifacts_editor" {
  bucket = yandex_storage_bucket.mlflow_artifacts.bucket
  role   = "storage.editor"

  members = [
    "serviceAccount:${yandex_iam_service_account.mlflow.id}",
  ]
}
