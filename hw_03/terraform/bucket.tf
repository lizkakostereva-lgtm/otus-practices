resource "yandex_storage_bucket" "spark_bucket" {
  bucket    = var.bucket_name
  folder_id = var.folder_id

  anonymous_access_flags {
    read = true
    list = true
  }

  force_destroy = true
}