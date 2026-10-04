# Service accounts.
#
# Yandex Cloud verifies the service account roles *at cluster-creation time*, so
# a wrong role here does not fail during `plan` - the API just refuses the
# cluster with "Permission denied". kubernetes.tf therefore depends_on every
# binding in this file.
#
# Role sets per docs.yandex.cloud/en/docs/managed-kubernetes/security/:
#
#   * Cluster (resource) SA - acts on our behalf over cluster nodes, the
#     pod/service subnets, disks and load balancers:
#       - k8s.clusters.agent - the minimum recommended role;
#       - vpc.publicAdmin    - required here because both the control plane and
#                              the worker nodes get public addresses.
#   * Node group SA - authenticates to the container registry:
#       - container-registry.images.puller - only needed for Yandex Container
#         Registry. We pull from GHCR, so it is not strictly required; it is
#         granted anyway so that a YC-registry mirror works without a code
#         change.
#
# The identity running `terraform apply` needs its own roles for this to work:
# k8s.editor or higher, iam.serviceAccounts.user, and vpc.publicAdmin for
# public access. Those are NOT granted from this code on purpose - escalating
# the caller's own privileges from inside the config it is deploying is how
# privilege escalation bugs happen.

resource "yandex_iam_service_account" "cluster" {
  name        = var.cluster_service_account_name
  description = "Managed Kubernetes control plane SA"
  folder_id   = var.folder_id
}

resource "yandex_iam_service_account" "nodes" {
  name        = var.node_service_account_name
  description = "Worker nodes SA"
  folder_id   = var.folder_id
}

# --- Cluster (resource) service account -------------------------------------

resource "yandex_resourcemanager_folder_iam_member" "cluster_k8s_clusters_agent" {
  folder_id = var.folder_id
  role      = "k8s.clusters.agent"
  member    = "serviceAccount:${yandex_iam_service_account.cluster.id}"
}

# Public control plane and public node IPs both need this. Without it the
# cluster is rejected with Permission denied even though every VPC resource
# already exists.
resource "yandex_resourcemanager_folder_iam_member" "cluster_vpc_public_admin" {
  folder_id = var.folder_id
  role      = "vpc.publicAdmin"
  member    = "serviceAccount:${yandex_iam_service_account.cluster.id}"
}

# Required by the master_logging block below. Without it the API rejects the
# cluster with:
#   "master logging require 'logging.writer' role to be assigned to master
#    serviceAccount"
# This covers the apiserver / audit / events / autoscaler log streams alike -
# they are all Cloud Logging writes by the control plane.
resource "yandex_resourcemanager_folder_iam_member" "cluster_logging_writer" {
  folder_id = var.folder_id
  role      = "logging.writer"
  member    = "serviceAccount:${yandex_iam_service_account.cluster.id}"
}

# --- Node group service account ----------------------------------------------

resource "yandex_resourcemanager_folder_iam_member" "nodes_registry_puller" {
  folder_id = var.folder_id
  role      = "container-registry.images.puller"
  member    = "serviceAccount:${yandex_iam_service_account.nodes.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "nodes_viewer" {
  folder_id = var.folder_id
  role      = "viewer"
  member    = "serviceAccount:${yandex_iam_service_account.nodes.id}"
}
