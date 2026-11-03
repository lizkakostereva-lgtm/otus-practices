output "network_id" {
  value = yandex_vpc_network.main.id
}

output "cluster_id" {
  value = yandex_kubernetes_cluster.main.id
}

output "cluster_name" {
  value = yandex_kubernetes_cluster.main.name
}

output "registry_id" {
  value = yandex_container_registry.main.id
}

output "registry_image" {
  value = "cr.yandex/${yandex_container_registry.main.id}/mlops-telecom-churn-api"
}

output "bucket_data" { value = yandex_storage_bucket.data.bucket }
output "bucket_dags" { value = yandex_storage_bucket.dags.bucket }
output "bucket_mlflow" { value = yandex_storage_bucket.mlflow.bucket }

output "s3_access_key" {
  value     = yandex_iam_service_account_static_access_key.mlflow_s3.access_key
  sensitive = true
}

output "s3_secret_key" {
  value     = yandex_iam_service_account_static_access_key.mlflow_s3.secret_key
  sensitive = true
}

output "postgres_public_ip" {
  value = yandex_compute_instance.postgres.network_interface[0].nat_ip_address
}

output "postgres_internal_ip" {
  value = yandex_compute_instance.postgres.network_interface[0].ip_address
}

output "mlflow_public_ip" {
  value = yandex_compute_instance.mlflow.network_interface[0].nat_ip_address
}

output "mlflow_tracking_uri" {
  value = "http://${yandex_compute_instance.mlflow.network_interface[0].nat_ip_address}:5000"
}

output "airflow_public_ip" {
  value = yandex_compute_instance.airflow.network_interface[0].nat_ip_address
}

output "airflow_ui" {
  value = "http://${yandex_compute_instance.airflow.network_interface[0].nat_ip_address}:8080"
}

output "ci_sa_key_json" {
  description = "Authorized key JSON for GitHub Actions secret YC_SA_KEY"
  value = jsonencode({
    id                 = yandex_iam_service_account_key.ci.id
    service_account_id = yandex_iam_service_account.ci.service_account_id
    created_at         = yandex_iam_service_account_key.ci.created_at
    key_algorithm      = "RSA_2048"
    public_key         = yandex_iam_service_account_key.ci.public_key
    private_key        = yandex_iam_service_account_key.ci.private_key
  })
  sensitive = true
}

output "services_subnet_id" {
  description = "Subnet for the Dataproc operators (cluster subnetwork, NAT-routed)"
  value       = yandex_vpc_subnet.services.id
}

output "dataproc_security_group_id" {
  description = "Security group passed to DataprocCreateClusterOperator"
  value       = yandex_vpc_security_group.dataproc.id
}

output "dataproc_service_account_id" {
  value = yandex_iam_service_account.dataproc.id
}

# Authorized key for the Airflow "yandexcloud" connection (see dataproc.tf):
# put it into the Extra field as {"service_account_json": <this JSON>, ...}
output "dataproc_sa_key_json" {
  value = jsonencode({
    id                 = yandex_iam_service_account_key.dataproc.id
    service_account_id = yandex_iam_service_account.dataproc.service_account_id
    created_at         = yandex_iam_service_account_key.dataproc.created_at
    key_algorithm      = "RSA_2048"
    public_key         = yandex_iam_service_account_key.dataproc.public_key
    private_key        = yandex_iam_service_account_key.dataproc.private_key
  })
  sensitive = true
}

output "kubeconfig_command" {
  value = "yc managed-kubernetes cluster get-credentials --id ${yandex_kubernetes_cluster.main.id} --external --force"
}
