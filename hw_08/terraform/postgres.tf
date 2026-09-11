# База данных метаданных MLflow — PostgreSQL на отдельной ВМ (postgres-vm).
# Установка и настройка выполняются Ansible (ansible/deploy_postgres.yml):
# apt postgresql, пользователь mlflow, БД mlflow, listen_addresses='*'.

resource "yandex_compute_instance" "postgres_vm" {
  name        = "postgres-vm"
  platform_id = var.postgres_vm_platform
  zone        = var.zone

  resources {
    cores  = var.postgres_vm_cores
    memory = var.postgres_vm_memory
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
      yandex_vpc_security_group.postgres_sg.id
    ]
  }

  metadata = {
    ssh-keys = "ubuntu:${var.ssh_public_key}"
  }

  allow_stopping_for_update = true
}
