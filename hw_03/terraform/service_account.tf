resource "yandex_iam_service_account" "dataproc_sa" {
  name = "dataproc-sa"
}

resource "yandex_resourcemanager_folder_iam_member" "dataproc_agent" {
  folder_id = var.folder_id
  role      = "dataproc.agent"
  member    = "serviceAccount:${yandex_iam_service_account.dataproc_sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "storage_admin" {
  folder_id = var.folder_id
  role      = "storage.admin"
  member    = "serviceAccount:${yandex_iam_service_account.dataproc_sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "viewer" {
  folder_id = var.folder_id
  role      = "viewer"
  member    = "serviceAccount:${yandex_iam_service_account.dataproc_sa.id}"
}