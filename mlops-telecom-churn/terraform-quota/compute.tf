data "yandex_compute_image" "ubuntu" {
  family = "ubuntu-2204-lts"
}

# Docker host: PostgreSQL + MLflow + Airflow (docker compose, see ansible-quota).
resource "yandex_compute_instance" "services" {
  name        = "mlops-churn-services"
  description = "PostgreSQL + MLflow + Airflow (docker compose)"
  platform_id = var.vm_platform
  zone        = var.zone_services

  resources {
    cores  = var.services_cores
    memory = var.services_memory
  }

  boot_disk {
    initialize_params {
      image_id = data.yandex_compute_image.ubuntu.id
      size     = var.vm_disk_size
      type     = "network-hdd"
    }
  }

  network_interface {
    subnet_id          = data.yandex_vpc_subnet.services.id
    nat                = true
    nat_ip_address     = yandex_vpc_address.services.external_ipv4_address[0].address
    security_group_ids = [yandex_vpc_security_group.services.id]
  }

  metadata = {
    ssh-keys = "ubuntu:${var.ssh_public_key}"
  }

  labels = {
    project = "mlops-telecom-churn"
    role    = "services"
  }

  allow_stopping_for_update = true
}

# Single-node Kubernetes (k3s) for the API, HPA and the monitoring stack.
resource "yandex_compute_instance" "k3s" {
  name        = "mlops-churn-k3s"
  description = "k3s: churn API, HPA, Prometheus/Grafana/Alertmanager"
  platform_id = var.vm_platform
  zone        = var.zone_k3s

  resources {
    cores  = var.k3s_cores
    memory = var.k3s_memory
  }

  boot_disk {
    initialize_params {
      image_id = data.yandex_compute_image.ubuntu.id
      size     = var.vm_disk_size
      type     = "network-hdd"
    }
  }

  network_interface {
    subnet_id          = data.yandex_vpc_subnet.k3s.id
    nat                = true
    nat_ip_address     = yandex_vpc_address.k3s.external_ipv4_address[0].address
    security_group_ids = [yandex_vpc_security_group.k3s.id]
  }

  metadata = {
    ssh-keys = "ubuntu:${var.ssh_public_key}"
  }

  labels = {
    project = "mlops-telecom-churn"
    role    = "k3s"
  }

  allow_stopping_for_update = true
}
