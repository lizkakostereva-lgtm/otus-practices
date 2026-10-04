# Managed Kubernetes: a regional 3-master control plane plus a node group of
# exactly var.node_count nodes (3 by default, one per zone).

resource "yandex_kubernetes_cluster" "main" {
  name        = var.cluster_name
  description = "3-node cluster for the url-fraud REST API (hw_09)"
  network_id  = yandex_vpc_network.main.id
  folder_id   = var.folder_id

  release_channel = "REGULAR"

  service_account_id      = yandex_iam_service_account.cluster.id
  node_service_account_id = yandex_iam_service_account.nodes.id

  # Pod and Service CIDRs. Keep the pod range out of the VPC ranges above.
  cluster_ipv4_ranges      = ["10.244.0.0/16"]
  service_ipv4_range       = "10.242.0.0/16"
  node_ipv4_cidr_mask_size = 24

  labels = {
    project = "url-fraud"
    env     = var.environment
  }

  master {
    version = var.kubernetes_version

    # Regional control plane: 3 masters, one per zone -> survives a zone loss.
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

    # A public control-plane endpoint lets kubectl connect from anywhere;
    # yc managed-kubernetes cluster get-kubeconfig --internal also works.
    public_ip = var.master_public_ip

    maintenance_policy {
      # The control plane has no auto_repair in this provider version.
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

  # YC validates the service account roles server-side when the cluster is
  # created and answers "Permission denied" if they are not in place yet.
  # Without these depends_on the bindings may still be in flight.
  depends_on = [
    yandex_resourcemanager_folder_iam_member.cluster_k8s_clusters_agent,
    yandex_resourcemanager_folder_iam_member.cluster_vpc_public_admin,
    yandex_resourcemanager_folder_iam_member.cluster_logging_writer,
    yandex_resourcemanager_folder_iam_member.nodes_registry_puller,
  ]
}

# Worker nodes: exactly var.node_count, fixed scale so "3 nodes" stays literal.
resource "yandex_kubernetes_node_group" "workers" {
  cluster_id  = yandex_kubernetes_cluster.main.id
  name        = "url-fraud-workers"
  version     = var.kubernetes_version
  description = "Worker nodes for the url-fraud API (hw_09)"

  labels = {
    project = "url-fraud"
  }

  node_labels = {
    project                          = "url-fraud"
    role                             = "worker"
    "node-role.kubernetes.io/worker" = ""
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

  # One node per zone. The zone is pinned here, while the subnets themselves
  # come from instance_template.network_interface.subnet_ids below -
  # allocation_policy.location.subnet_id is deprecated in provider 0.235.
  allocation_policy {
    dynamic "location" {
      for_each = toset(var.zones)
      content {
        zone = location.value
      }
    }
  }

  instance_template {
    platform_id = var.node_platform

    metadata = {
      "ssh-keys" = var.ssh_public_key != null ? "${trimspace(var.ssh_public_key)}\n" : null
      user-data  = null
    }

    # NAT is what exposes the NodePort endpoint on a node's public IP.
    network_interface {
      subnet_ids         = yandex_vpc_subnet.zones[*].id
      nat                = var.enable_public_ip_on_nodes
      ipv4               = true
      security_group_ids = [yandex_vpc_security_group.nodes.id]
    }

    resources {
      cores  = var.node_cores
      memory = var.node_memory_gb
      gpus   = 0
    }

    boot_disk {
      size = var.node_disk_size_gb
      type = var.node_disk_type
    }

    labels = {
      project = "url-fraud"
    }

    scheduling_policy {
      preemptible = false
    }
  }
}

# Read-only view of the instance group so the public node IPs can be printed.
data "yandex_compute_instance_group" "workers" {
  instance_group_id = yandex_kubernetes_node_group.workers.instance_group_id
}

# Optional: cluster-scoped RBAC for a human, e.g. ["userAccount:my-cloud-id:user/yc iam create-token --no-user-output"]
resource "yandex_kubernetes_cluster_iam_member" "extra_admins" {
  for_each = toset(var.cluster_admin_members)

  cluster_id = yandex_kubernetes_cluster.main.id
  role       = "cluster-admin"
  member     = each.value
}