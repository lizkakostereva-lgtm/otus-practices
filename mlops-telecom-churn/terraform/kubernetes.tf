# Managed Kubernetes: regional control plane (one master per zone) and a fixed
# node group with one worker per zone. In the full infrastructure there is no
# need to save cores, so this is the production-like setup.

resource "yandex_kubernetes_cluster" "main" {
  name        = var.cluster_name
  description = "MLOps telecom churn cluster"
  network_id  = yandex_vpc_network.main.id
  folder_id   = var.folder_id

  release_channel = "REGULAR"

  service_account_id      = yandex_iam_service_account.cluster.id
  node_service_account_id = yandex_iam_service_account.nodes.id

  cluster_ipv4_ranges      = ["10.244.0.0/16"]
  service_ipv4_range       = "10.242.0.0/16"
  node_ipv4_cidr_mask_size = 24

  master {
    version = var.kubernetes_version
    public_ip = true

    regional {
      region = "ru-central1"

      dynamic "location" {
        for_each = toset(var.zones)
        content {
          zone      = location.value
          subnet_id = local.master_subnets_by_zone[location.value]
        }
      }
    }

    security_group_ids = [yandex_vpc_security_group.master.id]

    maintenance_policy {
      auto_upgrade = true
    }

    master_logging {
      enabled                    = true
      events_enabled             = true
      audit_enabled              = true
      kube_apiserver_enabled     = true
      cluster_autoscaler_enabled = true
    }
  }

  depends_on = [
    yandex_resourcemanager_folder_iam_member.cluster_agent,
    yandex_resourcemanager_folder_iam_member.cluster_vpc,
    yandex_resourcemanager_folder_iam_member.cluster_logging,
    yandex_resourcemanager_folder_iam_member.nodes_puller,
  ]
}

resource "yandex_kubernetes_node_group" "workers" {
  cluster_id = yandex_kubernetes_cluster.main.id
  name       = "${var.cluster_name}-workers"
  version    = var.kubernetes_version

  labels = {
    project = "mlops-telecom-churn"
  }

  node_labels = {
    role = "worker"
  }

  scale_policy {
    fixed_scale {
      size = var.node_count
    }
  }

  maintenance_policy {
    auto_upgrade = true
    auto_repair  = true
  }

  allocation_policy {
    dynamic "location" {
      for_each = toset(var.zones)
      content {
        zone = location.value
      }
    }
  }

  instance_template {
    platform_id = var.vm_platform

    network_interface {
      subnet_ids         = yandex_vpc_subnet.nodes[*].id
      nat                = true
      security_group_ids = [yandex_vpc_security_group.nodes.id]
    }

    resources {
      cores  = var.node_cores
      memory = var.node_memory
    }

    boot_disk {
      size = var.node_disk_size
      type = var.node_disk_type
    }

    metadata = {
      ssh-keys = "ubuntu:${var.ssh_public_key}"
    }

    scheduling_policy {
      preemptible = false
    }
  }
}

data "yandex_compute_instance_group" "workers" {
  instance_group_id = yandex_kubernetes_node_group.workers.instance_group_id
}
