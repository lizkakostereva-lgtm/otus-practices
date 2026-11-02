# The quota stand reuses the existing `default` network and its per-zone
# subnets: creating a new VPC would consume the vpc.networks quota (2/2 used).

data "yandex_vpc_network" "default" {
  name = var.network_name
}

data "yandex_vpc_subnet" "services" {
  name      = var.subnet_services_name
  folder_id = var.folder_id
}

data "yandex_vpc_subnet" "k3s" {
  name      = var.subnet_k3s_name
  folder_id = var.folder_id
}

resource "yandex_vpc_address" "services" {
  name = "mlops-churn-services-ip"

  external_ipv4_address {
    zone_id = var.zone_services
  }
}

resource "yandex_vpc_address" "k3s" {
  name = "mlops-churn-k3s-ip"

  external_ipv4_address {
    zone_id = var.zone_k3s
  }
}

resource "yandex_vpc_security_group" "services" {
  name       = "mlops-churn-services-sg"
  network_id = data.yandex_vpc_network.default.id

  ingress {
    description    = "SSH"
    protocol       = "TCP"
    v4_cidr_blocks = ["0.0.0.0/0"]
    port           = 22
  }

  ingress {
    description    = "MLflow UI/API"
    protocol       = "TCP"
    v4_cidr_blocks = ["0.0.0.0/0"]
    port           = 5000
  }

  ingress {
    description    = "Airflow UI"
    protocol       = "TCP"
    v4_cidr_blocks = ["0.0.0.0/0"]
    port           = 8080
  }

  ingress {
    description    = "Internal (k3s pods -> MLflow/Postgres)"
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

resource "yandex_vpc_security_group" "k3s" {
  name       = "mlops-churn-k3s-sg"
  network_id = data.yandex_vpc_network.default.id

  ingress {
    description    = "SSH"
    protocol       = "TCP"
    v4_cidr_blocks = ["0.0.0.0/0"]
    port           = 22
  }

  ingress {
    description    = "k3s API server"
    protocol       = "TCP"
    v4_cidr_blocks = ["0.0.0.0/0"]
    port           = 6443
  }

  ingress {
    description    = "NodePort services (API, Grafana, Prometheus, Alertmanager)"
    protocol       = "TCP"
    v4_cidr_blocks = ["0.0.0.0/0"]
    from_port      = 30000
    to_port        = 32767
  }

  # TCP proxy to the services VM (MLflow 5000, Airflow 8080). The services
  # public IP is not reachable from every network, the k3s one is.
  ingress {
    description    = "MLflow UI (proxied)"
    protocol       = "TCP"
    v4_cidr_blocks = ["0.0.0.0/0"]
    port           = 5000
  }

  ingress {
    description    = "Airflow UI (proxied)"
    protocol       = "TCP"
    v4_cidr_blocks = ["0.0.0.0/0"]
    port           = 8080
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
