# Security group для нод временного Spark-кластера (создаётся и удаляется DAG'ом).
resource "yandex_vpc_security_group" "spark_sg" {
  name       = "spark-sg"
  network_id = var.network_id

  ingress {
    protocol       = "TCP"
    port           = 22
    v4_cidr_blocks = ["0.0.0.0/0"]
    description    = "SSH"
  }

  ingress {
    protocol       = "TCP"
    port           = 8888
    v4_cidr_blocks = ["0.0.0.0/0"]
    description    = "Jupyter"
  }

  ingress {
    protocol          = "ANY"
    predefined_target = "self_security_group"
    description       = "Dataproc internal traffic"
  }

  egress {
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}

# Security group для MLflow-сервера. TCP 5000 открывается для подсети,
# чтобы Data Proc и Managed Airflow (тоже в этой подсети) могли ходить в API MLflow.
data "yandex_vpc_subnet" "sub" {
  subnet_id = var.subnet_id
}

resource "yandex_vpc_security_group" "mlflow_sg" {
  name       = "mlflow-sg"
  network_id = var.network_id

  ingress {
    protocol       = "TCP"
    port           = 5000
    v4_cidr_blocks = data.yandex_vpc_subnet.sub.v4_cidr_blocks
    description    = "MLflow Tracking API (из подсети)"
  }

  ingress {
    protocol       = "TCP"
    port           = 22
    v4_cidr_blocks = ["0.0.0.0/0"]
    description    = "SSH (для Ansible и туннеля к UI)"
  }

  ingress {
    protocol          = "ANY"
    predefined_target = "self_security_group"
    description       = "mlflow-vm internal"
  }

  egress {
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}

# Security group для postgres-vm. 5432 открыт для подсети (MLflow ходит по internal IP).
resource "yandex_vpc_security_group" "postgres_sg" {
  name       = "postgres-sg"
  network_id = var.network_id

  ingress {
    protocol       = "TCP"
    port           = 5432
    v4_cidr_blocks = data.yandex_vpc_subnet.sub.v4_cidr_blocks
    description    = "PostgreSQL (из подсети)"
  }

  ingress {
    protocol       = "TCP"
    port           = 22
    v4_cidr_blocks = ["0.0.0.0/0"]
    description    = "SSH"
  }

  ingress {
    protocol          = "ANY"
    predefined_target = "self_security_group"
    description       = "postgres-vm internal"
  }

  egress {
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}
