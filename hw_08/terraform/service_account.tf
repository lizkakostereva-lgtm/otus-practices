# Сервисный аккаунт, под которым запускается Spark-кластер (создаётся DAG'ом).
# Роли необходимы для работы Data Proc кластера и доступа к Object Storage.

resource "yandex_iam_service_account" "dataproc_sa" {
  name = "dataproc-sa"
}

resource "yandex_resourcemanager_folder_iam_member" "dataproc_agent" {
  folder_id = var.folder_id
  role      = "dataproc.agent"
  member    = "serviceAccount:${yandex_iam_service_account.dataproc_sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "dataproc_provisioner" {
  folder_id = var.folder_id
  role      = "dataproc.provisioner"
  member    = "serviceAccount:${yandex_iam_service_account.dataproc_sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "storage_admin" {
  folder_id = var.folder_id
  role      = "storage.admin"
  member    = "serviceAccount:${yandex_iam_service_account.dataproc_sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "iam_user" {
  folder_id = var.folder_id
  role      = "iam.serviceAccounts.user"
  member    = "serviceAccount:${yandex_iam_service_account.dataproc_sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "viewer" {
  folder_id = var.folder_id
  role      = "viewer"
  member    = "serviceAccount:${yandex_iam_service_account.dataproc_sa.id}"
}

# Разрешаем airflow-sa создавать кластеры под dataproc-sa
resource "yandex_iam_service_account_iam_member" "dataproc_sa_user" {
  service_account_id = yandex_iam_service_account.dataproc_sa.id
  role               = "iam.serviceAccounts.user"
  member             = "serviceAccount:${yandex_iam_service_account.airflow_sa.id}"
}
