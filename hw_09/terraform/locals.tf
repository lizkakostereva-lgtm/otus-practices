# Zone -> subnet id maps.
#
# These exist because Terraform 1.5.x cannot parse a function call inside an
# index expression (e.g. resource[var.list.index(x)]), so the mapping is
# precomputed here and looked up by plain key afterwards.

locals {
  node_subnets_by_zone = {
    for idx, zone in var.zones : zone => yandex_vpc_subnet.zones[idx].id
  }

  master_subnets_by_zone = {
    for idx, zone in var.zones : zone => yandex_vpc_subnet.master[idx].id
  }

  node_subnet_cidrs_by_zone = {
    for idx, zone in var.zones : zone => element(var.network_cidr_blocks, idx)
  }

  # Short human label for tags.
  name_prefix = "url-fraud"
}