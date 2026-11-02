resource "yandex_container_registry" "main" {
  name      = "mlops-churn-registry"
  folder_id = var.folder_id
}
