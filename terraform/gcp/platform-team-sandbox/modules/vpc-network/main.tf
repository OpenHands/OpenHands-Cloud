# -----------------------------------------------------------------------------
# VPC Network Module for GKE Clusters
# -----------------------------------------------------------------------------

locals {
  # Collect all subnet CIDRs for firewall rules
  all_subnet_cidrs = concat(
    [var.subnet_cidr, var.pods_cidr, var.services_cidr],
    flatten([for s in var.additional_subnets : [s.cidr, s.pods_cidr, s.services_cidr]])
  )

  # Unique regions for NAT routers
  all_regions = distinct(concat([var.region], [for s in var.additional_subnets : s.region]))
}

resource "google_compute_network" "vpc" {
  name                    = var.network_name
  project                 = var.project_id
  auto_create_subnetworks = false
  routing_mode            = "GLOBAL"
}

# -----------------------------------------------------------------------------
# Primary Subnet
# -----------------------------------------------------------------------------

resource "google_compute_subnetwork" "subnet" {
  name          = var.subnet_name
  project       = var.project_id
  region        = var.region
  network       = google_compute_network.vpc.id
  ip_cidr_range = var.subnet_cidr

  secondary_ip_range {
    range_name    = "${var.subnet_name}-pods"
    ip_cidr_range = var.pods_cidr
  }

  secondary_ip_range {
    range_name    = "${var.subnet_name}-services"
    ip_cidr_range = var.services_cidr
  }

  private_ip_google_access = true
}

# -----------------------------------------------------------------------------
# Additional Subnets (for multi-cluster setups)
# -----------------------------------------------------------------------------

resource "google_compute_subnetwork" "additional" {
  for_each = { for idx, subnet in var.additional_subnets : subnet.name => subnet }

  name          = each.value.name
  project       = var.project_id
  region        = each.value.region
  network       = google_compute_network.vpc.id
  ip_cidr_range = each.value.cidr

  secondary_ip_range {
    range_name    = "${each.value.name}-pods"
    ip_cidr_range = each.value.pods_cidr
  }

  secondary_ip_range {
    range_name    = "${each.value.name}-services"
    ip_cidr_range = each.value.services_cidr
  }

  private_ip_google_access = true
}

# -----------------------------------------------------------------------------
# Cloud Router and NAT (one per region)
# -----------------------------------------------------------------------------

resource "google_compute_router" "router" {
  for_each = toset(local.all_regions)

  name    = "${var.network_name}-router-${each.key}"
  project = var.project_id
  region  = each.key
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  for_each = toset(local.all_regions)

  name                               = "${var.network_name}-nat-${each.key}"
  project                            = var.project_id
  router                             = google_compute_router.router[each.key].name
  region                             = each.key
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# -----------------------------------------------------------------------------
# Firewall Rules
# -----------------------------------------------------------------------------

# Firewall rule for internal communication across all subnets
resource "google_compute_firewall" "internal" {
  name    = "${var.network_name}-allow-internal"
  project = var.project_id
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = ["0-65535"]
  }

  allow {
    protocol = "udp"
    ports    = ["0-65535"]
  }

  allow {
    protocol = "icmp"
  }

  source_ranges = local.all_subnet_cidrs
}
