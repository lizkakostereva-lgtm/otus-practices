# Service accounts.
#
# * url-fraud-cluster-sa runs the cluster control plane (registry pulls for
#   addons, node management).
# * url-fraud-nodes-sa runs the worker pods. It needs container.deployer to pull
#   images from the *Yandex* registry; pulling from a public GHCR package needs
#   no extra role, but the pull secret does (see scripts/create_image_pull_secret.sh).

resource "yandex_iam_service_account" "cluster" {
  name        = var.cluster_service_account_name
  description = "Managed Kubernetes control plane SA"
  folder_id   = var.folder_id
}

resource "yandex_resourcemanager_folder_iam_member" "cluster_editor" {
  folder_id = var.folder_id
  role      = "k8s.admin"
  member    = "serviceAccount:${yandex_iam_service_account.cluster.id}"
}

resource "yandex_iam_service_account" "nodes" {
  name        = var.node_service_account_name
  description = "Worker nodes SA"
  folder_id   = var.folder_id
}

resource "yandex_resourcemanager_folder_iam_member" "nodes_container_deployer" {
  folder_id = var.folder_id
  role      = "container.deployer"
  member    = "serviceAccount:${yandex_iam_service_account.nodes.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "nodes_viewer" {
  folder_id = var.folder_id
  role      = "viewer"
  member    = "serviceAccount:${yandex_iam_service_account.nodes.id}"
}