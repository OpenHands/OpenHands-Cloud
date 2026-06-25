# -----------------------------------------------------------------------------
# Platform Team Sandbox Environment
# -----------------------------------------------------------------------------
# This environment deploys OpenHands to the Platform Team Sandbox GCP project.
# It uses sysbox for runtime isolation (same as production), not GKE Sandbox.
#
# Key differences from other environments:
# - Uses sysbox-runc for runtime isolation (enable_gke_sandbox = false)
# - Requires sysbox DaemonSet installation after terraform apply
# - Runtime nodes labeled with sysbox-install=yes and tainted with sysbox-runtime=true
# -----------------------------------------------------------------------------

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0.0"
    }
  }

  # Uncomment and configure for remote state
  # backend "gcs" {
  #   bucket = "platform-team-sandbox-terraform-state"
  #   prefix = "openhands/platform-team-sandbox"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# -----------------------------------------------------------------------------
# VPC Network
# -----------------------------------------------------------------------------

module "vpc" {
  source = "../../modules/vpc-network"

  project_id    = var.project_id
  region        = var.region
  network_name  = "${var.environment_name}-vpc"
  subnet_name   = "${var.environment_name}-subnet"
  subnet_cidr   = var.subnet_cidr
  pods_cidr     = var.pods_cidr
  services_cidr = var.services_cidr
}

# -----------------------------------------------------------------------------
# GKE Cluster (Single cluster with sysbox runtime nodes)
# -----------------------------------------------------------------------------

module "gke_cluster" {
  source = "../../modules/gke-cluster"

  project_id   = var.project_id
  location     = var.region
  cluster_name = "${var.environment_name}-cluster"

  network_name        = module.vpc.network_name
  subnet_name         = module.vpc.subnet_name
  pods_range_name     = module.vpc.pods_range_name
  services_range_name = module.vpc.services_range_name

  enable_autopilot        = var.enable_autopilot
  enable_private_nodes    = var.enable_private_nodes
  enable_private_endpoint = false
  master_ipv4_cidr_block  = var.master_ipv4_cidr_block

  master_authorized_networks = var.master_authorized_networks

  # Primary node pool configuration (for OpenHands app, Keycloak, etc.)
  node_machine_type       = var.node_machine_type
  node_disk_size_gb       = var.node_disk_size_gb
  node_pool_min_count     = var.node_pool_min_count
  node_pool_max_count     = var.node_pool_max_count
  node_pool_initial_count = var.node_pool_initial_count

  # Runtime node pool with sysbox (NOT GKE Sandbox)
  # This matches production configuration
  create_runtime_node_pool        = var.create_runtime_node_pool
  enable_gke_sandbox              = false  # Use sysbox, not gVisor
  runtime_node_machine_type       = var.runtime_node_machine_type
  runtime_node_disk_size_gb       = var.runtime_node_disk_size_gb
  runtime_node_pool_min_count     = var.runtime_node_pool_min_count
  runtime_node_pool_max_count     = var.runtime_node_pool_max_count
  runtime_node_pool_initial_count = var.runtime_node_pool_initial_count

  deletion_protection = var.deletion_protection

  labels = merge(var.labels, {
    environment    = var.environment_name
    routing-type   = "subdomain-based"
    cluster-type   = "single"
    runtime-type   = "sysbox"
  })
}

# -----------------------------------------------------------------------------
# Static IP for Ingress
# -----------------------------------------------------------------------------

resource "google_compute_global_address" "ingress_ip" {
  name    = "${var.environment_name}-ingress-ip"
  project = var.project_id
}

# -----------------------------------------------------------------------------
# DNS Zone (optional - create if managing DNS in this project)
# -----------------------------------------------------------------------------

resource "google_dns_managed_zone" "zone" {
  count = var.create_dns_zone ? 1 : 0

  name        = "${var.environment_name}-zone"
  project     = var.project_id
  dns_name    = "${var.domain}."
  description = "DNS zone for ${var.environment_name} OpenHands deployment"
}

# -----------------------------------------------------------------------------
# DNS Records for Subdomain Routing
# -----------------------------------------------------------------------------

# Root domain record
resource "google_dns_record_set" "root" {
  count = var.create_dns_zone ? 1 : 0

  name         = "${var.domain}."
  project      = var.project_id
  managed_zone = google_dns_managed_zone.zone[0].name
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_global_address.ingress_ip.address]
}

# Wildcard for all subdomains (app, auth, api, branches, etc.)
resource "google_dns_record_set" "wildcard" {
  count = var.create_dns_zone ? 1 : 0

  name         = "*.${var.domain}."
  project      = var.project_id
  managed_zone = google_dns_managed_zone.zone[0].name
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_global_address.ingress_ip.address]
}

# Double wildcard for nested subdomains (e.g., branch.auth.domain.com)
resource "google_dns_record_set" "double_wildcard" {
  count = var.create_dns_zone ? 1 : 0

  name         = "*.*.${var.domain}."
  project      = var.project_id
  managed_zone = google_dns_managed_zone.zone[0].name
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_global_address.ingress_ip.address]
}
