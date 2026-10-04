variable "cloud_id" {
  description = "Yandex Cloud cloud ID (yc cloud get --id)."
  type        = string
}

variable "folder_id" {
  description = "Folder the cluster is created in (yc config list)."
  type        = string
}

variable "token" {
  description = <<-EOT
    IAM token. Leave empty ("") to reuse the `yc` CLI profile, which is the
    recommended option — then the token never enters terraform.tfvars.
    Generate a short-lived one with: yc iam create-token
  EOT
  type        = string
  default     = ""
  sensitive   = true
}

variable "cluster_name" {
  description = "Name of the Managed Kubernetes cluster."
  type        = string
  default     = "url-fraud-cluster"
}

variable "environment" {
  description = "Environment tag; also used as the terraform workspace default."
  type        = string
  default     = "production"
}

# --- networking -----------------------------------------------------------
variable "zones" {
  description = "Availability zones for the cluster nodes. One subnet per zone."
  type        = list(string)
  default     = ["ru-central1-a", "ru-central1-b", "ru-central1-d"]
}

variable "network_cidr_blocks" {
  description = <<-EOT
    Node subnet CIDR per zone, same order as var.zones. Must not overlap with
    each other, with var.master_network_cidr_blocks, or with the pod/service
    ranges (10.244.0.0/16 and 10.242.0.0/16).
  EOT
  type        = list(string)
  default = [
    "10.130.0.0/24",
    "10.131.0.0/24",
    "10.132.0.0/24",
  ]
}

variable "master_network_cidr_blocks" {
  description = "Control-plane subnet CIDR per zone, same order as var.zones."
  type        = list(string)
  default = [
    "10.140.0.0/24",
    "10.141.0.0/24",
    "10.142.0.0/24",
  ]
}

variable "zone" {
  description = "Default zone for resources that need exactly one (e.g. the public IP)."
  type        = string
  default     = "ru-central1-a"
}

# --- kubernetes -----------------------------------------------------------
variable "kubernetes_version" {
  description = "Managed Kubernetes version. Check with: yc managed-kubernetes list-versions"
  type        = string
  default     = "1.33"
}

variable "node_count" {
  description = "Number of nodes in the group (the homework asks for 3)."
  type        = number
  default     = 3
}

variable "node_platform" {
  description = "Node platform ID. standard-v3 is the cheapest sane default."
  type        = string
  default     = "standard-v3"
}

variable "node_cores" {
  type    = number
  default = 2
}

variable "node_memory_gb" {
  type    = number
  default = 4
}

variable "node_disk_size_gb" {
  type    = number
  default = 30
}

variable "node_disk_type" {
  description = "Root disk type for the nodes. network-ssd is faster than network-hdd."
  type        = string
  default     = "network-ssd"
}

variable "enable_public_ip_on_nodes" {
  description = <<-EOT
    Give every node a public IP. Required for the NodePort demo endpoint
    (http://<node-ip>:30080). Turn it off and use a Load Balancer instead.
  EOT
  type        = bool
  default     = true
}

variable "node_public_ip_allowlist" {
  description = "CIDRs allowed to reach the nodes over SSH (NodePort stays open to the world)."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "node_port" {
  description = "NodePort used by k8s/30-service-nodeport.yaml. Must match that manifest."
  type        = number
  default     = 30080
}

variable "master_public_ip" {
  description = <<-EOT
    Give the Kubernetes API server a public endpoint so `kubectl` works from
    outside the VPC. Turn off when using an internal endpoint only.
  EOT
  type        = bool
  default     = true
}

variable "cluster_admin_members" {
  description = <<-EOT
    Optional cluster-scoped cluster-admin bindings, e.g.
    ["userAccount:<cloud-id>:user/my-login"] or serviceAccount:<id>.
    Empty by default.
  EOT
  type        = list(string)
  default     = []
}

# --- optional extras ------------------------------------------------------
variable "enable_autoscaler" {
  description = "Install the cluster autoscaler so node groups can grow."
  type        = bool
  default     = true
}

variable "enable_metrics_server" {
  description = "Install metrics-server so kubectl top and the HPA work."
  type        = bool
  default     = true
}

variable "cluster_service_account_name" {
  description = "Service account the cluster itself runs as."
  type        = string
  default     = "url-fraud-cluster-sa"
}

variable "node_service_account_name" {
  description = "Service account attached to the worker nodes."
  type        = string
  default     = "url-fraud-nodes-sa"
}

variable "api_port" {
  description = "Public port opened on the nodes' security group for the API."
  type        = number
  default     = 30080
}

variable "ssh_public_key" {
  description = "SSH public key installed on the nodes for debugging."
  type        = string
  default     = null
}

variable "labels" {
  description = "Extra labels applied to the cluster and its nodes."
  type        = map(string)
  default     = {}
}

variable "tags" {
  description = "Extra resource tags applied to the folder resources."
  type        = list(string)
  default     = ["hw09", "url-fraud"]
}