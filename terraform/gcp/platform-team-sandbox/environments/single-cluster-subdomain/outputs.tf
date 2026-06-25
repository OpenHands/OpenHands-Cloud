output "cluster_name" {
  description = "Name of the GKE cluster"
  value       = module.gke_cluster.cluster_name
}

output "cluster_endpoint" {
  description = "Endpoint of the GKE cluster"
  value       = module.gke_cluster.cluster_endpoint
  sensitive   = true
}

output "get_credentials_command" {
  description = "Command to get cluster credentials"
  value       = module.gke_cluster.get_credentials_command
}

output "ingress_ip" {
  description = "Static IP address for ingress"
  value       = google_compute_global_address.ingress_ip.address
}

output "network_name" {
  description = "Name of the VPC network"
  value       = module.vpc.network_name
}

output "dns_zone_name" {
  description = "Name of the DNS zone (if created)"
  value       = var.create_dns_zone ? google_dns_managed_zone.zone[0].name : null
}

output "dns_name_servers" {
  description = "DNS name servers (if zone created)"
  value       = var.create_dns_zone ? google_dns_managed_zone.zone[0].name_servers : null
}

output "environment_info" {
  description = "Summary of the environment"
  value = {
    environment_name = var.environment_name
    routing_type     = "subdomain-based"
    cluster_type     = "single"
    domain           = var.domain
    cluster_name     = module.gke_cluster.cluster_name
    ingress_ip       = google_compute_global_address.ingress_ip.address
  }
}
