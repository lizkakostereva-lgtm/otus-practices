# Managed Service for Apache Kafka (Yandex Cloud) for the online inference pipeline.
#
# Топология потоковых данных:
#   provider (kafka_producer.py)  ->  topic "fraud-transactions"
#   Spark Structured Streaming (fraud_streaming_inference.py) читает
#   "fraud-transactions", прогоняет модель из MLflow и пишет предсказания
#   в topic "fraud-predictions".
#
# Кластер создаётся ОДИН раз Terraform'ом (в отличие от временного Data Proc
# кластера, который живёт только во время запуска DAG). Kafka — «брокер
# сообщений», его не пересоздают при каждом запуске.
#
# Для ДЗ достаточно одного брокера: при 400 TPS и ~1-10 КБ на сообщение
# одиночный брокер легко держит нагрузку (пиковая пропускная способность
# одного брокера s2.micro — десятки тысяч сообщений в секунду).

# --- Кластер Kafka -----------------------------------------------------------
resource "yandex_mdb_kafka_cluster" "fraud_kafka" {
  name               = var.kafka_cluster_name
  environment        = var.kafka_environment
  network_id         = var.network_id
  folder_id          = var.folder_id
  subnet_ids         = [var.subnet_id]
  security_group_ids = [yandex_vpc_security_group.kafka_sg.id]

  deletion_protection = false

  config {
    version          = var.kafka_version
    assign_public_ip = var.kafka_assign_public_ip
    schema_registry  = false
    # Зона размещения брокеров Kafka (должна соответствовать subnet_ids/подсети).
    zones = [var.zone]

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

    # ZooKeeper используется версией Apache Kafka 3.6 и ниже;
    # для Kafka 3.7+ (KRaft) блок zookeeper не требуется.
    zookeeper {
      resources {
        resource_preset_id = var.kafka_zookeeper_resource_preset
        disk_type_id       = var.kafka_disk_type
        disk_size          = var.kafka_zookeeper_disk_size
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

# --- Топики ----------------------------------------------------------------
resource "yandex_mdb_kafka_topic" "input_topic" {
  cluster_id         = yandex_mdb_kafka_cluster.fraud_kafka.id
  name               = var.kafka_input_topic
  partitions         = var.kafka_input_partitions
  replication_factor = var.kafka_replication_factor
}

resource "yandex_mdb_kafka_topic" "output_topic" {
  cluster_id         = yandex_mdb_kafka_cluster.fraud_kafka.id
  name               = var.kafka_output_topic
  partitions         = var.kafka_output_partitions
  replication_factor = var.kafka_replication_factor
}

# --- Пользователь Kafka ----------------------------------------------------
resource "yandex_mdb_kafka_user" "fraud_user" {
  cluster_id = yandex_mdb_kafka_cluster.fraud_kafka.id
  name       = var.kafka_user_name
  password   = var.kafka_user_password

  permission {
    topic_name = yandex_mdb_kafka_topic.input_topic.name
    role       = "ACCESS_ROLE_TOPIC_ADMIN"
  }

  permission {
    topic_name = yandex_mdb_kafka_topic.output_topic.name
    role       = "ACCESS_ROLE_TOPIC_ADMIN"
  }
}