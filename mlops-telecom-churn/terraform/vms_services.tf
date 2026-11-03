data "yandex_compute_image" "ubuntu" {
  family = "ubuntu-2204-lts"
}

# --- PostgreSQL (MLflow backend store) ------------------------------------

resource "yandex_compute_instance" "postgres" {
  name        = "mlops-churn-postgres"
  platform_id = var.vm_platform
  zone        = var.zone

  resources {
    cores  = var.postgres_vm_cores
    memory = var.postgres_vm_memory
  }

  boot_disk {
    initialize_params {
      image_id = data.yandex_compute_image.ubuntu.id
      size     = var.vm_disk_size
      type     = var.vm_disk_type
    }
  }

  network_interface {
    subnet_id          = yandex_vpc_subnet.services.id
    nat                = true
    security_group_ids = [yandex_vpc_security_group.postgres.id]
  }

  metadata = {
    ssh-keys = "ubuntu:${var.ssh_public_key}"
  }

  allow_stopping_for_update = true
}

# --- MLflow tracking server ----------------------------------------------

resource "yandex_compute_instance" "mlflow" {
  name        = "mlops-churn-mlflow"
  platform_id = var.vm_platform
  zone        = var.zone

  resources {
    cores  = var.mlflow_vm_cores
    memory = var.mlflow_vm_memory
  }

  boot_disk {
    initialize_params {
      image_id = data.yandex_compute_image.ubuntu.id
      size     = var.vm_disk_size
      type     = var.vm_disk_type
    }
  }

  network_interface {
    subnet_id          = yandex_vpc_subnet.services.id
    nat                = true
    security_group_ids = [yandex_vpc_security_group.services.id]
  }

  metadata = {
    ssh-keys = "ubuntu:${var.ssh_public_key}"
  }

  allow_stopping_for_update = true
}

# --- Airflow (retraining scheduler) --------------------------------------

resource "yandex_compute_instance" "airflow" {
  name        = "mlops-churn-airflow"
  platform_id = var.vm_platform
  zone        = var.zone

  resources {
    cores  = var.airflow_vm_cores
    memory = var.airflow_vm_memory
  }

  boot_disk {
    initialize_params {
      image_id = data.yandex_compute_image.ubuntu.id
      size     = var.vm_disk_size
      type     = var.vm_disk_type
    }
  }

  network_interface {
    subnet_id          = yandex_vpc_subnet.services.id
    nat                = true
    security_group_ids = [yandex_vpc_security_group.services.id]
  }

  metadata = {
    ssh-keys = "ubuntu:${var.ssh_public_key}"
  }

  allow_stopping_for_update = true
}
