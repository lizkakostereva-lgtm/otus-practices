resource "yandex_storage_bucket" "spark_bucket" {
  bucket    = var.bucket_name
  folder_id = var.folder_id

  force_destroy = true
}
