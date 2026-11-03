# Spark (Yandex Data Processing) for the full infrastructure.
#
# The Spark cluster is NOT created by Terraform: retraining happens rarely
# (weekly), so a persistent cluster would idle and burn quota/budget. Following
# the course approach, the DAG creates a temporary cluster per run:
#   - DataprocCreateClusterOperator    (airflow/dags/churn_spark_dataproc_dag.py)
#   - DataprocCreatePysparkJobOperator (spark/preprocess_churn.py, spark/train_churn.py)
#   - DataprocDeleteClusterOperator
# Terraform only provides what the DAG cannot create itself: the security group
# and the service account the operators assume.

resource "yandex_vpc_security_group" "dataproc" {
  name       = "${var.network_name}-dataproc-sg"
  network_id = yandex_vpc_network.main.id

  ingress {
    description       = "Dataproc internal traffic"
    protocol          = "ANY"
    predefined_target = "self_security_group"
  }

  ingress {
    description    = "SSH"
    protocol       = "TCP"
    port           = 22
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
    from_port      = 0
    to_port        = 65535
  }
}

# Service account assumed by the Dataproc operators.
# It needs to create/delete the cluster and its nodes (dataproc.editor,
# compute related roles are granted by dataproc.editor over the folder) and
# read the job scripts from the S3 bucket.
resource "yandex_iam_service_account" "dataproc" {
  name        = "mlops-churn-dataproc-sa"
  description = "Airflow Dataproc operators: temporary Spark cluster per retrain"
}

resource "yandex_resourcemanager_folder_iam_member" "dataproc_editor" {
  folder_id = var.folder_id
  role      = "dataproc.editor"
  member    = "serviceAccount:${yandex_iam_service_account.dataproc.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "dataproc_agent" {
  folder_id = var.folder_id
  role      = "dataproc.agent"
  member    = "serviceAccount:${yandex_iam_service_account.dataproc.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "dataproc_vpc_user" {
  folder_id = var.folder_id
  role      = "vpc.user"
  member    = "serviceAccount:${yandex_iam_service_account.dataproc.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "dataproc_storage" {
  folder_id = var.folder_id
  role      = "storage.editor"
  member    = "serviceAccount:${yandex_iam_service_account.dataproc.id}"
}

resource "yandex_iam_service_account_key" "dataproc" {
  service_account_id = yandex_iam_service_account.dataproc.id
  description        = "Authorized key for the Airflow yandexcloud connection"
}