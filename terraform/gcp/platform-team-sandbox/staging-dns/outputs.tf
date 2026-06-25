# -----------------------------------------------------------------------------
# Outputs for OpenHands Enterprise Staging DNS
# -----------------------------------------------------------------------------

output "zone_name" {
  description = "Name of the created DNS zone"
  value       = google_dns_managed_zone.staging.name
}

output "zone_dns_name" {
  description = "DNS name of the zone"
  value       = google_dns_managed_zone.staging.dns_name
}

output "name_servers" {
  description = "Name servers for the staging zone"
  value       = google_dns_managed_zone.staging.name_servers
}

output "wildcard_domain" {
  description = "Wildcard domain for staging deployments"
  value       = "*.ohe-staging.platform-team.all-hands.dev"
}

output "base_domain" {
  description = "Base domain for staging deployments"
  value       = "ohe-staging.platform-team.all-hands.dev"
}
