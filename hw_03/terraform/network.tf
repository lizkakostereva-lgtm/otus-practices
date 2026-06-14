resource "yandex_vpc_gateway" "nat_gateway" {
  name = "spark-nat"

  shared_egress_gateway {}
}

resource "yandex_vpc_route_table" "nat_rt" {
  network_id = var.network_id

  static_route {
    destination_prefix = "0.0.0.0/0"
    gateway_id         = yandex_vpc_gateway.nat_gateway.id
  }
}
