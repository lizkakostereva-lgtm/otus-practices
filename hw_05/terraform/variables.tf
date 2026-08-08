variable "cloud_id" {}

variable "folder_id" {}

variable "subnet_id" {}

variable "network_id" {}

variable "zone" {
  default = "ru-central1-a"
}

variable "token" {}

variable "bucket_name" {
  default = "spark-bucket-ek"
}

variable "ssh_public_key" {}

variable "airflow_cluster_name" {
  default = "airflow-cluster"
}

variable "dags_bucket_name" {
  default = "airflow-dags-bucket-ek"
}

variable "airflow_admin_password" {}
