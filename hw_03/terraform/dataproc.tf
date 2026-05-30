resource "yandex_dataproc_cluster" "spark_cluster" {

  name        = var.cluster_name
  description = "Spark cluster for homework"

  bucket = yandex_storage_bucket.spark_bucket.bucket

  service_account_id = yandex_iam_service_account.dataproc_sa.id

  zone_id = var.zone

  security_group_ids = [
    yandex_vpc_security_group.spark_sg.id
  ]

  cluster_config {

    version_id = "2.1"

    hadoop {

      services = [
        "SPARK",
        "HDFS",
        "YARN"
      ]

      ssh_public_keys = [
        var.ssh_public_key
      ]

      oslogin = false
    }

    subcluster_spec {

      name = "master"

      role = "MASTERNODE"

      assign_public_ip = true

      subnet_id = yandex_vpc_subnet.spark_subnet.id

      resources {
        resource_preset_id = "s3-c2-m8"
        disk_type_id       = "network-hdd"
        disk_size          = 40
      }

      hosts_count = 1
    }

    subcluster_spec {

      name = "data"

      role = "DATANODE"

      subnet_id = yandex_vpc_subnet.spark_subnet.id

      resources {
        resource_preset_id = "s3-c4-m16"
        disk_type_id       = "network-hdd"
        disk_size          = 128
      }

      hosts_count = 3
    }
  }

  depends_on = [
    yandex_resourcemanager_folder_iam_member.dataproc_agent,
    yandex_resourcemanager_folder_iam_member.storage_admin,
    yandex_resourcemanager_folder_iam_member.viewer
  ]
}