output "services_public_ip" {
  value = yandex_compute_instance.services.network_interface[0].nat_ip_address
}

output "k3s_public_ip" {
  value = yandex_compute_instance.k3s.network_interface[0].nat_ip_address
}

output "services_private_ip" {
  value = yandex_compute_instance.services.network_interface[0].ip_address
}

# MLflow and Airflow are published through the k3s VM (TCP proxy), because the
# services VM public IP is not reachable from every client network.
output "mlflow_tracking_uri" {
  value = "http://${yandex_compute_instance.k3s.network_interface[0].nat_ip_address}:5000"
}

output "airflow_ui" {
  value = "http://${yandex_compute_instance.k3s.network_interface[0].nat_ip_address}:8080"
}

output "api_nodeport" {
  value = "http://${yandex_compute_instance.k3s.network_interface[0].nat_ip_address}:30080"
}

output "grafana_url" {
  value = "http://${yandex_compute_instance.k3s.network_interface[0].nat_ip_address}:30300"
}

output "prometheus_url" {
  value = "http://${yandex_compute_instance.k3s.network_interface[0].nat_ip_address}:30900"
}

output "alertmanager_url" {
  value = "http://${yandex_compute_instance.k3s.network_interface[0].nat_ip_address}:30903"
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
  value     = yandex_iam_service_account_static_access_key.s3.access_key
  sensitive = true
}

output "s3_secret_key" {
  value     = yandex_iam_service_account_static_access_key.s3.secret_key
  sensitive = true
}

output "sa_key_json" {
  description = "Authorized key JSON (docker login cr.yandex / k8s imagePullSecret / GitHub secret YC_SA_KEY)"
  value = jsonencode({
    id                 = yandex_iam_service_account_key.ci.id
    service_account_id = yandex_iam_service_account_key.ci.service_account_id
    created_at         = yandex_iam_service_account_key.ci.created_at
    key_algorithm      = "RSA_2048"
    private_key        = yandex_iam_service_account_key.ci.private_key
  })
  sensitive = true
}
