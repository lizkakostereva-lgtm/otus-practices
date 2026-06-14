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


variable "cluster_name" {
  default = "spark-cluster"
}