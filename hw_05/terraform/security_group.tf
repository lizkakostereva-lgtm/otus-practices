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