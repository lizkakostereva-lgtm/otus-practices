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
