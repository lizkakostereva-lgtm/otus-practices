variable "cloud_id" {}

variable "folder_id" {}

variable "subnet_id" {}

variable "network_id" {}

variable "zone" {
  default = "ru-central1-a"
}

variable "token" {}

variable "bucket_name" {
  default = "spark-bucket"
}

variable "ssh_public_key" {}
variable "security_group_ids" {
  type = list(string)
}

variable "service_account_id" {}

variable "cluster_name" {
  default = "spark-cluster"
}