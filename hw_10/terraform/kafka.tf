# OPTIONAL: Yandex Managed Service for Apache Kafka.
#
# Disabled by default (var.enable_managed_kafka = false) because the homework
# ships a self-contained single-broker Kafka in kafka/k8s/10-kafka.yaml. Set the
# variable to true and re-point the `KAFKA_BOOTSTRAP` value in
# kafka/k8s/20-attack.yaml (and the DAG env) at this cluster's FQDN to run the
# same attack against a managed, replicated broker.

resource "yandex_vpc_security_group" "kafka" {
  count = var.enable_managed_kafka ? 1 : 0

  name        = "url-fraud-kafka-sg"
  description = "Managed Kafka: broker and client traffic inside the VPC"
  network_id  = yandex_vpc_network.main.id
  folder_id   = var.folder_id

  ingress {
    protocol          = "TCP"
    port              = 9091
    predefined_target = "self_security_group"
    description       = "broker-to-broker"
  }

  ingress {
    protocol          = "TCP"
    port              = 9092
    predefined_target = "self_security_group"
    description       = "client access from the cluster"
  }

  egress {
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
    description    = "egress"
  }

  labels = {
    project = "url-fraud"
  }
}

resource "yandex_mdb_kafka_cluster" "main" {
  count = var.enable_managed_kafka ? 1 : 0

  name               = var.kafka_cluster_name
  environment        = "PRODUCTION"
  network_id         = yandex_vpc_network.main.id
  folder_id          = var.folder_id
  subnet_ids         = [yandex_vpc_subnet.zones[0].id]
  security_group_ids = [yandex_vpc_security_group.kafka[0].id]

  deletion_protection = false

  config {
    version          = var.kafka_version
    assign_public_ip = false
    zones            = [element(var.zones, 0)]

    kafka {
      resources {
        resource_preset_id = var.kafka_broker_resource_preset
        disk_type_id       = var.kafka_disk_type
        disk_size          = var.kafka_broker_disk_size
      }

      kafka_config {
        compression_type = "COMPRESSION_TYPE_ZSTD"
      }
    }
  }

  maintenance_window {
    type = "ANYTIME"
  }

  timeouts {
    read = "15m"
  }
}

resource "yandex_mdb_kafka_topic" "urls" {
  count = var.enable_managed_kafka ? 1 : 0

  cluster_id         = yandex_mdb_kafka_cluster.main[0].id
  name               = var.kafka_topic
  partitions         = 3
  replication_factor = 1
}

resource "yandex_mdb_kafka_user" "attack" {
  count = var.enable_managed_kafka ? 1 : 0

  cluster_id = yandex_mdb_kafka_cluster.main[0].id
  name       = "url-fraud-attack"
  password   = var.kafka_user_password

  permission {
    topic_name = yandex_mdb_kafka_topic.urls[0].name
    role       = "ACCESS_ROLE_TOPIC_ADMIN"
  }
}
