# Dedicated VPC for the full MLOps infrastructure.
# A NAT gateway is required because the default subnets have no route table and
# worker nodes could not pull images from the container registry otherwise.

resource "yandex_vpc_network" "main" {
  name      = var.network_name
  folder_id = var.folder_id
}

resource "yandex_vpc_gateway" "nat" {
  name      = "${var.network_name}-nat"
  folder_id = var.folder_id

  shared_egress_gateway {}
}

resource "yandex_vpc_route_table" "main" {
  name       = "${var.network_name}-rt"
  network_id = yandex_vpc_network.main.id
  folder_id  = var.folder_id

  static_route {
    destination_prefix = "0.0.0.0/0"
    gateway_id         = yandex_vpc_gateway.nat.id
  }
}

resource "yandex_vpc_subnet" "nodes" {
  count = length(var.zones)

  name           = "${var.network_name}-nodes-${element(var.zones, count.index)}"
  network_id     = yandex_vpc_network.main.id
  zone           = element(var.zones, count.index)
  v4_cidr_blocks = [element(var.node_cidr_blocks, count.index)]
  route_table_id = yandex_vpc_route_table.main.id
  folder_id      = var.folder_id
}

resource "yandex_vpc_subnet" "masters" {
  count = length(var.zones)

  name           = "${var.network_name}-masters-${element(var.zones, count.index)}"
  network_id     = yandex_vpc_network.main.id
  zone           = element(var.zones, count.index)
  v4_cidr_blocks = [element(var.master_cidr_blocks, count.index)]
  route_table_id = yandex_vpc_route_table.main.id
  folder_id      = var.folder_id
}

resource "yandex_vpc_subnet" "services" {
  name           = "${var.network_name}-services"
  network_id     = yandex_vpc_network.main.id
  zone           = var.zone
  v4_cidr_blocks = [var.services_cidr]
  route_table_id = yandex_vpc_route_table.main.id
  folder_id      = var.folder_id
}

locals {
  master_subnets_by_zone = {
    for idx, zone in var.zones : zone => yandex_vpc_subnet.masters[idx].id
  }
}

# --- Security groups ------------------------------------------------------

resource "yandex_vpc_security_group" "master" {
  name       = "${var.network_name}-master-sg"
  network_id = yandex_vpc_network.main.id

  ingress {
    description    = "Kubernetes API"
    protocol       = "TCP"
    v4_cidr_blocks = ["0.0.0.0/0"]
    port           = 6443
  }

  ingress {
    description    = "HTTPS"
    protocol       = "TCP"
    v4_cidr_blocks = ["0.0.0.0/0"]
    port           = 443
  }

  ingress {
    description    = "Internal"
    protocol       = "ANY"
    v4_cidr_blocks = ["10.0.0.0/8"]
  }

  egress {
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
    from_port      = 0
    to_port        = 65535
  }
}

resource "yandex_vpc_security_group" "nodes" {
  name       = "${var.network_name}-nodes-sg"
  network_id = yandex_vpc_network.main.id

  ingress {
    description    = "NodePort services"
    protocol       = "TCP"
    v4_cidr_blocks = ["0.0.0.0/0"]
    from_port      = 30000
    to_port        = 32767
  }

  ingress {
    description    = "SSH"
    protocol       = "TCP"
    v4_cidr_blocks = ["0.0.0.0/0"]
    port           = 22
  }

  ingress {
    description    = "Internal"
    protocol       = "ANY"
    v4_cidr_blocks = ["10.0.0.0/8"]
  }

  egress {
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
    from_port      = 0
    to_port        = 65535
  }
}

resource "yandex_vpc_security_group" "services" {
  name       = "${var.network_name}-services-sg"
  network_id = yandex_vpc_network.main.id

  dynamic "ingress" {
    for_each = [22, 5000, 8080]
    content {
      description    = "Service port ${ingress.value}"
      protocol       = "TCP"
      v4_cidr_blocks = ["0.0.0.0/0"]
      port           = ingress.value
    }
  }

  ingress {
    description    = "Internal"
    protocol       = "ANY"
    v4_cidr_blocks = ["10.0.0.0/8"]
  }

  egress {
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
    from_port      = 0
    to_port        = 65535
  }
}

resource "yandex_vpc_security_group" "postgres" {
  name       = "${var.network_name}-postgres-sg"
  network_id = yandex_vpc_network.main.id

  ingress {
    description    = "SSH"
    protocol       = "TCP"
    v4_cidr_blocks = ["0.0.0.0/0"]
    port           = 22
  }

  ingress {
    description    = "PostgreSQL from the VPC"
    protocol       = "TCP"
    v4_cidr_blocks = ["10.0.0.0/8"]
    port           = 5432
  }

  egress {
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
    from_port      = 0
    to_port        = 65535
  }
}
