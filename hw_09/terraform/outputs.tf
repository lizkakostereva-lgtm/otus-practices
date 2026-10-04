output "cluster_id" {
  description = "Managed Kubernetes cluster ID"
  value       = yandex_kubernetes_cluster.main.id
}

output "cluster_name" {
  value = yandex_kubernetes_cluster.main.name
}

output "kubernetes_version" {
  value = var.kubernetes_version
}

output "cluster_status" {
  value = yandex_kubernetes_cluster.main.status
}

output "master_endpoints" {
  description = "External endpoint of the Kubernetes API server."
  value       = try(yandex_kubernetes_cluster.main.master[0].external_v4_endpoint, null)
}

output "node_group_name" {
  value = yandex_kubernetes_node_group.workers.name
}

output "node_count" {
  description = "Number of nodes in the worker node group (the homework asks for 3)."
  value       = var.node_count
}

output "network_id" {
  value = yandex_vpc_network.main.id
}

output "node_subnet_ids" {
  description = "One subnet per availability zone."
  value       = yandex_vpc_subnet.zones[*].id
}

output "master_subnet_ids" {
  value = yandex_vpc_subnet.master[*].id
}

output "subnet_cidrs" {
  description = "Node subnet CIDR per zone."
  value       = local.node_subnet_cidrs_by_zone
}

output "security_group_ids" {
  description = "Security groups attached to masters and nodes."
  value = {
    master = [yandex_vpc_security_group.master.id]
    nodes  = [yandex_vpc_security_group.nodes.id]
  }
}

output "node_public_ips" {
  description = "Public IPs of the worker nodes, in zone order. Empty when NAT is disabled."
  value = [
    for nic in data.yandex_compute_instance_group.workers.instances :
    try(nic.network_interface[0].nat_ip_address, "")
  ]
}

output "node_internal_ips" {
  value = [
    for nic in data.yandex_compute_instance_group.workers.instances :
    try(nic.network_interface[0].ip_address, "")
  ]
}

output "node_zones" {
  value = data.yandex_compute_instance_group.workers.instances[*].zone_id
}

output "api_endpoint" {
  description = <<-EOT
    Public base URL of the API once the k8s manifests are applied.
    Any node's public IP works, because the Service is a NodePort.
  EOT
  value = join("", [
    "http://",
    try(data.yandex_compute_instance_group.workers.instances[0].network_interface[0].nat_ip_address, ""),
    ":",
    tostring(var.node_port),
  ])
}

output "smoke_command" {
  description = "Ready-to-paste verification command."
  value = format(
    "API_BASE_URL=%s ./scripts/smoke_test.sh",
    "http://${try(data.yandex_compute_instance_group.workers.instances[0].network_interface[0].nat_ip_address, "<node-ip>")}:${var.node_port}",
  )
}

output "get_kubeconfig_command" {
  description = "Command that writes a kubeconfig for this cluster."
  value = format(
    "make kubeconfig   # или напрямую: yc managed-kubernetes cluster get-credentials --name %s --region ru-central1 --format yaml > ~/.kube/config-url-fraud-cluster",
    yandex_kubernetes_cluster.main.name,
  )
}