output "network_name" {
  description = "The name of the VPC network"
  value       = google_compute_network.vpc.name
}

output "network_id" {
  description = "The ID of the VPC network"
  value       = google_compute_network.vpc.id
}

output "network_self_link" {
  description = "The self-link of the VPC network"
  value       = google_compute_network.vpc.self_link
}

# -----------------------------------------------------------------------------
# Primary Subnet Outputs
# -----------------------------------------------------------------------------

output "subnet_name" {
  description = "The name of the primary subnet"
  value       = google_compute_subnetwork.subnet.name
}

output "subnet_id" {
  description = "The ID of the primary subnet"
  value       = google_compute_subnetwork.subnet.id
}

output "subnet_self_link" {
  description = "The self-link of the primary subnet"
  value       = google_compute_subnetwork.subnet.self_link
}

output "pods_range_name" {
  description = "The name of the secondary range for pods"
  value       = "${var.subnet_name}-pods"
}

output "services_range_name" {
  description = "The name of the secondary range for services"
  value       = "${var.subnet_name}-services"
}

# -----------------------------------------------------------------------------
# Additional Subnet Outputs (for multi-cluster)
# -----------------------------------------------------------------------------

output "additional_subnet_names" {
  description = "Names of additional subnets"
  value       = [for s in google_compute_subnetwork.additional : s.name]
}

output "additional_subnet_ids" {
  description = "IDs of additional subnets"
  value       = [for s in google_compute_subnetwork.additional : s.id]
}

output "additional_subnet_self_links" {
  description = "Self-links of additional subnets"
  value       = [for s in google_compute_subnetwork.additional : s.self_link]
}

output "additional_pods_range_names" {
  description = "Names of secondary ranges for pods in additional subnets"
  value       = [for name, s in google_compute_subnetwork.additional : "${name}-pods"]
}

output "additional_services_range_names" {
  description = "Names of secondary ranges for services in additional subnets"
  value       = [for name, s in google_compute_subnetwork.additional : "${name}-services"]
}

# -----------------------------------------------------------------------------
# All Subnets (combined)
# -----------------------------------------------------------------------------

output "all_subnet_names" {
  description = "All subnet names including primary and additional"
  value       = concat([google_compute_subnetwork.subnet.name], [for s in google_compute_subnetwork.additional : s.name])
}

output "all_subnet_cidrs" {
  description = "All subnet CIDR ranges"
  value       = concat([var.subnet_cidr], [for s in var.additional_subnets : s.cidr])
}
