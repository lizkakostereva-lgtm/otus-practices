resource "yandex_storage_bucket" "spark_bucket" {
  bucket = var.bucket_name
  folder_id = var.folder_id
  anonymous_access_flags {
    read = true
    list = true
  }

  force_destroy = true
}

resource "yandex_dataproc_cluster" "spark_cluster" {
  name        = var.cluster_name
  description = "Spark cluster for homework"

  bucket = yandex_storage_bucket.spark_bucket.bucket

  service_account_id = var.service_account_id

  zone_id = var.zone

  security_group_ids = var.security_group_ids

  cluster_config {
    version_id = "2.1"

    hadoop {
      services = ["SPARK", "HDFS", "YARN"]
      properties = {
      }
      ssh_public_keys = [var.ssh_public_key]
      oslogin = false
    }

    subcluster_spec {
      name = "master"

      role = "MASTERNODE"

      assign_public_ip = true


      subnet_id = var.subnet_id

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

      subnet_id = var.subnet_id

      resources {
        resource_preset_id = "s3-c4-m16"
        disk_type_id       = "network-hdd"
        disk_size          = 128
      }

      hosts_count = 3
    }
  }
}