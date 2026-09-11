variable "cloud_id" {}

variable "folder_id" {}

variable "subnet_id" {}

variable "network_id" {}

variable "zone" {
  default = "ru-central1-a"
}

variable "token" {}

variable "ssh_public_key" {}

variable "bucket_name" {
  default = "spark-bucket-ek"
}

variable "dags_bucket_name" {
  default = "airflow-dags-bucket-ek"
}

variable "airflow_cluster_name" {
  default = "airflow-cluster"
}

variable "airflow_admin_password" {}

# --- MLflow ---
variable "mlflow_bucket_name" {
  default = "mlflow-bucket-ek"
}

variable "mlflow_pg_password" {
  description = "Password of the 'mlflow' user in PostgreSQL on postgres-vm (letters + digits)"
}

variable "mlflow_vm_platform" {
  default = "standard-v3"
}

variable "mlflow_vm_cores" {
  default = 2
}

variable "mlflow_vm_memory" {
  default = 4
}

# --- PostgreSQL VM ---
variable "postgres_vm_platform" {
  default = "standard-v3"
}

variable "postgres_vm_cores" {
  default = 2
}

variable "postgres_vm_memory" {
  default = 4
}

# --- Managed Kafka ---
variable "kafka_cluster_name" {
  default = "fraud-kafka"
}

variable "kafka_environment" {
  default = "PRODUCTION"
}

variable "kafka_version" {
  default     = "3.9"
  description = "Apache Kafka version. 3.6 deprecated by Yandex; 3.7+ uses KRaft, but ZooKeeper is still supported up to 3.9."
}

variable "kafka_broker_resource_preset" {
  default = "s2.micro"
}

variable "kafka_broker_disk_size" {
  default = 25
}

variable "kafka_zookeeper_resource_preset" {
  default = "s2.micro"
}

variable "kafka_zookeeper_disk_size" {
  default = 10
}

variable "kafka_disk_type" {
  default = "network-hdd"
}

variable "kafka_assign_public_ip" {
  default = false
}

variable "kafka_user_name" {
  default = "fraud-user"
}

variable "kafka_user_password" {
  description = "Password for the Kafka user (fraud-user). Used by producer/consumer."
  sensitive   = true
}

variable "kafka_input_topic" {
  default = "fraud-transactions"
}

variable "kafka_output_topic" {
  default = "fraud-predictions"
}

variable "kafka_input_partitions" {
  default = 6
}

variable "kafka_output_partitions" {
  default = 6
}

variable "kafka_replication_factor" {
  default = 1
}