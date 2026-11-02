variable "token" {
  type      = string
  sensitive = true
}

variable "cloud_id" { type = string }
variable "folder_id" { type = string }

variable "zone_services" {
  description = "Zone for the MLflow/Postgres/Airflow VM (subnet default-ru-central1-d)"
  type        = string
  default     = "ru-central1-d"
}

variable "zone_k3s" {
  description = "Zone for the k3s VM (subnet default-ru-central1-a). A different zone is used because the trial quota allows only 2 vCPU per zone."
  type        = string
  default     = "ru-central1-a"
}

variable "network_name" {
  type    = string
  default = "default"
}

variable "subnet_services_name" {
  type    = string
  default = "default-ru-central1-d"
}

variable "subnet_k3s_name" {
  type    = string
  default = "default-ru-central1-a"
}

variable "bucket_data_name" { type = string }
variable "bucket_dags_name" { type = string }
variable "bucket_mlflow_name" { type = string }

variable "ssh_public_key" { type = string }

variable "vm_platform" {
  type    = string
  default = "standard-v3"
}

variable "services_cores" {
  type    = number
  default = 2
}

variable "services_memory" {
  type    = number
  default = 6
}

variable "k3s_cores" {
  type    = number
  default = 2
}

variable "k3s_memory" {
  type    = number
  default = 12
}

variable "vm_disk_size" {
  description = "GB. The trial HDD quota is 20 GB per zone."
  type        = number
  default     = 20
}
