# Yandex Container Registry for the API image.
#
# Images are pushed from CI/GitHub Actions (cr.yandex/<registry_id>/url-fraud-api)
# and pulled by the worker nodes using the node group service account, which
# already has container-registry.images.puller (service_account.tf) — so no
# imagePullSecret is required on the cluster.

resource "yandex_container_registry" "main" {
  name      = "url-fraud-registry"
  folder_id = var.folder_id

  labels = {
    project = "url-fraud"
  }
}