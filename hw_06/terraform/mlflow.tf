# MLflow Tracking Server на отдельной ВМ (mlflow-vm).
# Установка выполняется Ansible (ansible/deploy_mlflow.yml): venv + mlflow + systemd.
# Метаданные — PostgreSQL на postgres-vm, артефакты (модели) — в S3.

# Сервисный аккаунт MLflow: статический ключ используется и сервером, и
# PySpark-джобой для загрузки артефактов в Object Storage.
resource "yandex_iam_service_account" "mlflow_sa" {
  name = "mlflow-sa"
}

resource "yandex_resourcemanager_folder_iam_member" "mlflow_storage_editor" {
  folder_id = var.folder_id
  role      = "storage.editor"
  member    = "serviceAccount:${yandex_iam_service_account.mlflow_sa.id}"
}

resource "yandex_iam_service_account_static_access_key" "mlflow_sa_key" {
  service_account_id = yandex_iam_service_account.mlflow_sa.id
  description        = "Static access key for MLflow (S3 artifacts)"
}

data "yandex_compute_image" "ubuntu" {
  family = "ubuntu-2204-lts"
}

resource "yandex_compute_instance" "mlflow_vm" {
  name        = "mlflow-vm"
  platform_id = var.mlflow_vm_platform
  zone        = var.zone

  resources {
    cores  = var.mlflow_vm_cores
    memory = var.mlflow_vm_memory
  }

  boot_disk {
    initialize_params {
      image_id = data.yandex_compute_image.ubuntu.id
      size     = 20
      type     = "network-hdd"
    }
  }

  network_interface {
    subnet_id = var.subnet_id
    nat       = true

    security_group_ids = [
      yandex_vpc_security_group.mlflow_sg.id
    ]
  }

  metadata = {
    ssh-keys = "ubuntu:${var.ssh_public_key}"
  }

  allow_stopping_for_update = true
}
