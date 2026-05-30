resource "yandex_vpc_network" "spark_network" {
  name = "spark-network"
}

resource "yandex_vpc_gateway" "nat_gateway" {
  name = "spark-nat"

  shared_egress_gateway {}
}

resource "yandex_vpc_route_table" "nat_rt" {
  network_id = yandex_vpc_network.spark_network.id

  static_route {
    destination_prefix = "0.0.0.0/0"
    gateway_id         = yandex_vpc_gateway.nat_gateway.id
  }
}

resource "yandex_vpc_subnet" "spark_subnet" {
  name           = "spark-subnet"
  zone           = var.zone
  network_id     = yandex_vpc_network.spark_network.id
  v4_cidr_blocks = ["10.10.0.0/24"]

  route_table_id = yandex_vpc_route_table.nat_rt.id
}