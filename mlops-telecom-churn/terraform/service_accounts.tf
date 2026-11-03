# Service accounts and keys used by the pipeline, CI and the cluster.

resource "yandex_iam_service_account" "mlflow" {
  name        = "mlops-churn-mlflow-sa"
  description = "MLflow server: S3 artifact access"
}

resource "yandex_resourcemanager_folder_iam_member" "mlflow_storage" {
  folder_id = var.folder_id
  role      = "storage.editor"
  member    = "serviceAccount:${yandex_iam_service_account.mlflow.id}"
}

resource "yandex_iam_service_account_static_access_key" "mlflow_s3" {
  service_account_id = yandex_iam_service_account.mlflow.id
  description        = "Static key for MLflow S3 artifacts and pipeline runs"
}

resource "yandex_iam_service_account" "cluster" {
  name        = "mlops-churn-cluster-sa"
  description = "Managed Kubernetes control plane"
}

resource "yandex_iam_service_account" "nodes" {
  name        = "mlops-churn-nodes-sa"
  description = "Managed Kubernetes worker nodes"
}

resource "yandex_resourcemanager_folder_iam_member" "cluster_agent" {
  folder_id = var.folder_id
  role      = "k8s.clusters.agent"
  member    = "serviceAccount:${yandex_iam_service_account.cluster.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "cluster_vpc" {
  folder_id = var.folder_id
  role      = "vpc.publicAdmin"
  member    = "serviceAccount:${yandex_iam_service_account.cluster.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "cluster_logging" {
  folder_id = var.folder_id
  role      = "logging.writer"
  member    = "serviceAccount:${yandex_iam_service_account.cluster.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "nodes_puller" {
  folder_id = var.folder_id
  role      = "container-registry.images.puller"
  member    = "serviceAccount:${yandex_iam_service_account.nodes.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "nodes_viewer" {
  folder_id = var.folder_id
  role      = "viewer"
  member    = "serviceAccount:${yandex_iam_service_account.nodes.id}"
}

# CI service account: pushes images and can deploy (optional CD job).
resource "yandex_iam_service_account" "ci" {
  name        = "mlops-churn-ci-sa"
  description = "GitHub Actions: push images, deploy to Kubernetes"
}

resource "yandex_resourcemanager_folder_iam_member" "ci_pusher" {
  folder_id = var.folder_id
  role      = "container-registry.images.pusher"
  member    = "serviceAccount:${yandex_iam_service_account.ci.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "ci_puller" {
  folder_id = var.folder_id
  role      = "container-registry.images.puller"
  member    = "serviceAccount:${yandex_iam_service_account.ci.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "ci_storage" {
  folder_id = var.folder_id
  role      = "storage.editor"
  member    = "serviceAccount:${yandex_iam_service_account.ci.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "ci_k8s" {
  folder_id = var.folder_id
  role      = "k8s.editor"
  member    = "serviceAccount:${yandex_iam_service_account.ci.id}"
}

resource "yandex_iam_service_account_key" "ci" {
  service_account_id = yandex_iam_service_account.ci.id
  description        = "Authorized key for docker login / GitHub Actions"
}
