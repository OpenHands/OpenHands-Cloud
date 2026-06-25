# -----------------------------------------------------------------------------
# Platform Team Sandbox - Outputs
# -----------------------------------------------------------------------------

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
    runtime_type     = "sysbox"
    domain           = var.domain
    cluster_name     = module.gke_cluster.cluster_name
    ingress_ip       = google_compute_global_address.ingress_ip.address
  }
}

# -----------------------------------------------------------------------------
# Sysbox-specific outputs
# -----------------------------------------------------------------------------

output "sysbox_install_command" {
  description = "Command to install sysbox DaemonSet after cluster creation"
  value       = "kubectl apply -f ../../../../testenv-charts/k8s/sysbox/sysbox-install.yaml"
}

output "sysbox_verify_command" {
  description = "Command to verify sysbox installation"
  value       = "kubectl get pods -n sysbox -o wide && kubectl get runtimeclass sysbox-runc"
}

output "post_terraform_steps" {
  description = "Steps to complete after terraform apply"
  value       = <<-EOT
    
    After terraform apply completes, run these commands:
    
    1. Get cluster credentials:
       ${module.gke_cluster.get_credentials_command}
    
    2. Install sysbox:
       kubectl apply -f ../../../../testenv-charts/k8s/sysbox/sysbox-install.yaml
    
    3. Wait for sysbox pods to be ready:
       kubectl wait --for=condition=Ready pods -n sysbox --all --timeout=300s
    
    4. Verify sysbox RuntimeClass is available:
       kubectl get runtimeclass sysbox-runc
    
    5. Deploy OpenHands with runtime-api configured for sysbox:
       - Set RUNTIME_CLASS: "sysbox-runc" in values
       - Set warmRuntimes nodeSelector: { "sysbox-install": "yes" }
       - Set warmRuntimes tolerations: [{"key": "sysbox-runtime", "value": "true", "effect": "NoSchedule"}]
    
  EOT
}
