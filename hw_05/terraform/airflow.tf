resource "yandex_iam_service_account" "airflow_sa" {
  name = "airflow-sa"
}

resource "yandex_resourcemanager_folder_iam_member" "airflow_integration" {
  folder_id = var.folder_id
  role      = "managed-airflow.integrationProvider"
  member    = "serviceAccount:${yandex_iam_service_account.airflow_sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "airflow_storage" {
  folder_id = var.folder_id
  role      = "storage.editor"
  member    = "serviceAccount:${yandex_iam_service_account.airflow_sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "airflow_vpc_user" {
  folder_id = var.folder_id
  role      = "vpc.user"
  member    = "serviceAccount:${yandex_iam_service_account.airflow_sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "airflow_dataproc_editor" {
  folder_id = var.folder_id
  role      = "dataproc.editor"
  member    = "serviceAccount:${yandex_iam_service_account.airflow_sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "airflow_dataproc_agent" {
  folder_id = var.folder_id
  role      = "dataproc.agent"
  member    = "serviceAccount:${yandex_iam_service_account.airflow_sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "airflow_compute_editor" {
  folder_id = var.folder_id
  role      = "compute.editor"
  member    = "serviceAccount:${yandex_iam_service_account.airflow_sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "airflow_iam_user" {
  folder_id = var.folder_id
  role      = "iam.serviceAccounts.user"
  member    = "serviceAccount:${yandex_iam_service_account.airflow_sa.id}"
}

resource "yandex_iam_service_account_key" "airflow_sa_key" {
  service_account_id = yandex_iam_service_account.airflow_sa.id
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

resource "yandex_airflow_cluster" "airflow" {
  name               = var.airflow_cluster_name
  subnet_ids         = [var.subnet_id]
  service_account_id = yandex_iam_service_account.airflow_sa.id
  admin_password     = var.airflow_admin_password
  folder_id          = var.folder_id

  airflow_version = "3.1"
  python_version  = "3.12"

  deletion_protection = false

  code_sync = {
    s3 = {
      bucket = yandex_storage_bucket.airflow_dags_bucket.bucket
    }
  }

  lockbox_secrets_backend = {
    enabled = false
  }

  webserver = {
    count              = 1
    resource_preset_id = "c1-m4"
  }

  scheduler = {
    count              = 1
    resource_preset_id = "c1-m4"
  }

  dag_processor = {
    count              = 1
    resource_preset_id = "c1-m4"
  }

  worker = {
    min_count          = 1
    max_count          = 2
    resource_preset_id = "c1-m4"
  }

  maintenance_window = {
    type = "ANYTIME"
  }

  logging = {
    enabled   = true
    folder_id = var.folder_id
    min_level = "INFO"
  }

  depends_on = [
    yandex_resourcemanager_folder_iam_member.airflow_integration,
    yandex_resourcemanager_folder_iam_member.airflow_storage,
    yandex_resourcemanager_folder_iam_member.airflow_vpc_user,
    yandex_resourcemanager_folder_iam_member.airflow_dataproc_editor,
    yandex_resourcemanager_folder_iam_member.airflow_dataproc_agent,
    yandex_resourcemanager_folder_iam_member.airflow_compute_editor,
    yandex_resourcemanager_folder_iam_member.airflow_iam_user,
  ]
}
