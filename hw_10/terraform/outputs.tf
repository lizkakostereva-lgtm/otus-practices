output "registry_id" {
  description = "ID of the Yandex Container Registry (image base: cr.yandex/<id>/url-fraud-api)."
  value       = yandex_container_registry.main.id
}

output "registry_image" {
  description = "Full image base reference in the Yandex Container Registry."
  value       = "cr.yandex/${yandex_container_registry.main.id}/url-fraud-api"
}

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
# --- hw_10 additions ---------------------------------------------------------

output "registry_api_image" {
  description = "Base reference for the API image: <ref>:<tag>."
  value       = "cr.yandex/${yandex_container_registry.main.id}/url-fraud-api"
}

output "registry_retrain_image" {
  description = "Base reference for the retraining job image used by Airflow."
  value       = "cr.yandex/${yandex_container_registry.main.id}/url-fraud-retrain"
}

output "registry_attack_image" {
  description = "Base reference for the Kafka attack simulator image."
  value       = "cr.yandex/${yandex_container_registry.main.id}/url-fraud-attack"
}

output "registry_mlflow_image" {
  description = "Base reference for the MLflow server image."
  value       = "cr.yandex/${yandex_container_registry.main.id}/mlflow"
}

output "mlflow_bucket" {
  description = "Object Storage bucket used as the MLflow artifact store."
  value       = yandex_storage_bucket.mlflow_artifacts.bucket
}

output "mlflow_s3_access_key_id" {
  description = "Static key id MLflow uses for S3 (feed into the mlflow-secrets k8s secret)."
  value       = yandex_iam_service_account_static_access_key.mlflow.access_key
  sensitive   = true
}

output "mlflow_s3_secret_key" {
  description = "Static secret MLflow uses for S3."
  value       = yandex_iam_service_account_static_access_key.mlflow.secret_key
  sensitive   = true
}

output "mlflow_tracking_url" {
  description = "In-cluster MLflow tracking URI."
  value       = "http://mlflow.url-fraud.svc.cluster.local:5000"
}

output "airflow_url" {
  description = "Airflow web UI (NodePort 30090 on any node public IP)."
  value = format(
    "http://%s:30090",
    try(data.yandex_compute_instance_group.workers.instances[0].network_interface[0].nat_ip_address, "<node-ip>"),
  )
}

output "grafana_url" {
  description = "Grafana UI (NodePort 30300)."
  value = format(
    "http://%s:30300",
    try(data.yandex_compute_instance_group.workers.instances[0].network_interface[0].nat_ip_address, "<node-ip>"),
  )
}

output "kafka_bootstrap_servers" {
  description = "Kafka bootstrap address (managed cluster FQDN, or empty for in-cluster)."
  value = try(
    format("%s.mdb.yandexcloud.net:9091", yandex_mdb_kafka_cluster.main[0].name),
    "",
  )
}
