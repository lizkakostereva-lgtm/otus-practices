# Auth precedence: an explicit `token` in terraform.tfvars wins, otherwise the
# provider reuses the `yc` CLI profile (~/.config/yc/config).
#
# NOTE on `zone`: the Managed Kubernetes *control plane* always lives in
# ru-central1. var.zone is used only for the VPC subnets and the node group.
provider "yandex" {
  cloud_id  = var.cloud_id
  folder_id = var.folder_id
  zone      = "ru-central1"

  token = var.token
}