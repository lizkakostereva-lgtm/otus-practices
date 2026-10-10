# Dedicated VPC for this homework.
#
# Why a new network instead of the shared `default` one used in hw_02..hw_08:
#   * the default subnets have no route table, so nodes get no internet egress
#     and cannot pull images from the container registry;
#   * keeping this homework's resources separate makes `terraform destroy` safe.

resource "yandex_vpc_network" "main" {
  name        = "url-fraud-net"
  description = "VPC for the url-fraud API (hw_09)"
  folder_id   = var.folder_id

  labels = {
    project = "url-fraud"
  }
}

# Shared egress so every subnet can reach the internet (image pulls, APIs).
resource "yandex_vpc_gateway" "nat" {
  name        = "url-fraud-nat"
  description = "Shared egress gateway for the cluster subnets"
  folder_id   = var.folder_id

  shared_egress_gateway {}

  labels = {
    project = "url-fraud"
  }
}

resource "yandex_vpc_route_table" "main" {
  name        = "url-fraud-rt"
  description = "Default route via the shared egress gateway"
  network_id  = yandex_vpc_network.main.id
  folder_id   = var.folder_id

  static_route {
    destination_prefix = "0.0.0.0/0"
    gateway_id         = yandex_vpc_gateway.nat.id
  }
}

# One subnet per zone so the 3 nodes really land in 3 different zones.
resource "yandex_vpc_subnet" "zones" {
  count = length(var.zones)

  name           = "url-fraud-${element(var.zones, count.index)}"
  description    = "Node subnet for ${element(var.zones, count.index)}"
  network_id     = yandex_vpc_network.main.id
  zone           = element(var.zones, count.index)
  v4_cidr_blocks = [element(var.network_cidr_blocks, count.index)]
  route_table_id = yandex_vpc_route_table.main.id
  folder_id      = var.folder_id

  labels = {
    project = "url-fraud"
  }
}

# Control-plane master subnets live in the same zones but must not overlap the
# node subnets (masters also get 10.244.0.0/16 for pods).
resource "yandex_vpc_subnet" "master" {
  count = length(var.zones)

  name           = "url-fraud-master-${element(var.zones, count.index)}"
  description    = "Control plane subnet for ${element(var.zones, count.index)}"
  network_id     = yandex_vpc_network.main.id
  zone           = element(var.zones, count.index)
  v4_cidr_blocks = [element(var.master_network_cidr_blocks, count.index)]
  route_table_id = yandex_vpc_route_table.main.id
  folder_id      = var.folder_id

  labels = {
    project = "url-fraud"
  }
}

# Security group for the control plane: only the managed cluster itself.
resource "yandex_vpc_security_group" "master" {
  name        = "url-fraud-master-sg"
  description = "Control plane: API + etcd traffic inside the group"
  network_id  = yandex_vpc_network.main.id
  folder_id   = var.folder_id

  ingress {
    protocol          = "ANY"
    port              = 443
    predefined_target = "self_security_group"
    description       = "apiserver / etcd peer traffic"
  }

  ingress {
    protocol          = "ANY"
    predefined_target = "self_security_group"
    description       = "masters internal traffic"
  }

  egress {
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
    description    = "control plane egress"
  }

  labels = {
    project = "url-fraud"
  }
}

# Security group for the worker nodes.
resource "yandex_vpc_security_group" "nodes" {
  name        = "url-fraud-nodes-sg"
  description = "Kubernetes nodes: API over SSH and the NodePort HTTP endpoint"
  network_id  = yandex_vpc_network.main.id
  folder_id   = var.folder_id

  # SSH for debugging nodes (only when a key is provided).
  dynamic "ingress" {
    for_each = var.ssh_public_key != null ? [1] : []
    content {
      protocol       = "TCP"
      port           = 22
      v4_cidr_blocks = var.node_public_ip_allowlist
      description    = "SSH"
    }
  }

  # Public API endpoint (NodePort).
  ingress {
    protocol       = "TCP"
    port           = var.api_port
    v4_cidr_blocks = ["0.0.0.0/0"]
    description    = "REST API via NodePort"
  }

  # kubelet, CNI and node-to-node traffic inside the group.
  ingress {
    protocol          = "ANY"
    port              = 10250
    predefined_target = "self_security_group"
    description       = "kubelet"
  }

  ingress {
    protocol          = "ANY"
    predefined_target = "self_security_group"
    description       = "nodes internal traffic"
  }

  egress {
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
    description    = "image pulls, updates, egress"
  }

  labels = {
    project = "url-fraud"
  }
}