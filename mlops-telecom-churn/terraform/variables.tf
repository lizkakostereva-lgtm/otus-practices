variable "token" {
  description = "OAuth / IAM token. Leave empty to reuse the yc CLI profile."
  type        = string
  default     = ""
  sensitive   = true
}

variable "cloud_id" { type = string }
variable "folder_id" { type = string }

variable "zone" {
  description = "Default zone for single-zone resources"
  type        = string
  default     = "ru-central1-d"
}

variable "zones" {
  description = "Zones for the managed Kubernetes node group and subnets"
  type        = list(string)
  default     = ["ru-central1-a", "ru-central1-b", "ru-central1-d"]
}

variable "network_name" {
  type    = string
  default = "mlops-churn-net"
}

variable "node_cidr_blocks" {
  description = "One node subnet per zone"
  type        = list(string)
  default     = ["10.150.1.0/24", "10.150.2.0/24", "10.150.3.0/24"]
}

variable "master_cidr_blocks" {
  description = "One control-plane subnet per zone"
  type        = list(string)
  default     = ["10.150.11.0/24", "10.150.12.0/24", "10.150.13.0/24"]
}

variable "services_cidr" {
  description = "Subnet for the MLflow / Postgres / Airflow VMs"
  type        = string
  default     = "10.150.10.0/24"
}

variable "bucket_data_name" { type = string }
variable "bucket_dags_name" { type = string }
variable "bucket_mlflow_name" { type = string }

variable "ssh_public_key" { type = string }

variable "airflow_admin_password" {
  type      = string
  sensitive = true
}

# --- VM sizing (full infrastructure, not quota-bound) ---------------------
variable "vm_platform" {
  type    = string
  default = "standard-v3"
}

variable "postgres_vm_cores" {
  type    = number
  default = 2
}

variable "postgres_vm_memory" {
  type    = number
  default = 4
}

variable "mlflow_vm_cores" {
  type    = number
  default = 2
}

variable "mlflow_vm_memory" {
  type    = number
  default = 4
}

variable "airflow_vm_cores" {
  type    = number
  default = 2
}

variable "airflow_vm_memory" {
  type    = number
  default = 4
}

variable "vm_disk_size" {
  type    = number
  default = 30
}

variable "vm_disk_type" {
  type    = string
  default = "network-ssd"
}

# --- Kubernetes -----------------------------------------------------------
variable "cluster_name" {
  type    = string
  default = "mlops-churn-cluster"
}

variable "kubernetes_version" {
  type    = string
  default = "1.33"
}

variable "node_count" {
  type    = number
  default = 3
}

variable "node_cores" {
  type    = number
  default = 2
}

variable "node_memory" {
  type    = number
  default = 4
}

variable "node_disk_size" {
  type    = number
  default = 30
}

variable "node_disk_type" {
  type    = string
  default = "network-ssd"
}
