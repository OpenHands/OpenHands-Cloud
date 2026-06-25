# -----------------------------------------------------------------------------
# OpenHands Enterprise Staging DNS Infrastructure
# 
# Creates a subdomain zone for staging environments with wildcard DNS support
# enabling developers to access deployments via predictable URLs like:
#   <branch-name>.ohe-staging.platform-team.all-hands.dev
# -----------------------------------------------------------------------------

terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 4.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# -----------------------------------------------------------------------------
# DNS Zone for staging environments
# -----------------------------------------------------------------------------

resource "google_dns_managed_zone" "staging" {
  name        = "ohe-staging-platform-team-all-hands-dot-dev"
  dns_name    = "ohe-staging.platform-team.all-hands.dev."
  description = "DNS zone for OpenHands Enterprise staging environments"

  labels = {
    environment = "staging"
    managed-by  = "terraform"
    team        = "platform"
  }

  visibility = "public"
}

# -----------------------------------------------------------------------------
# NS delegation record in parent zone
# -----------------------------------------------------------------------------

resource "google_dns_record_set" "staging_ns_delegation" {
  name         = "ohe-staging.platform-team.all-hands.dev."
  managed_zone = var.parent_zone_name
  type         = "NS"
  ttl          = 300

  rrdatas = google_dns_managed_zone.staging.name_servers
}

# -----------------------------------------------------------------------------
# Wildcard A record for all staging deployments
# Routes all *.ohe-staging.platform-team.all-hands.dev to Traefik LB
# -----------------------------------------------------------------------------

resource "google_dns_record_set" "wildcard" {
  name         = "*.ohe-staging.platform-team.all-hands.dev."
  managed_zone = google_dns_managed_zone.staging.name
  type         = "A"
  ttl          = 60

  rrdatas = [var.traefik_lb_ip]
}

# Root A record for the staging zone itself
resource "google_dns_record_set" "root" {
  name         = "ohe-staging.platform-team.all-hands.dev."
  managed_zone = google_dns_managed_zone.staging.name
  type         = "A"
  ttl          = 60

  rrdatas = [var.traefik_lb_ip]
}
