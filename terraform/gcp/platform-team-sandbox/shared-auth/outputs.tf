# -----------------------------------------------------------------------------
# Outputs for Shared Keycloak Infrastructure
# -----------------------------------------------------------------------------

output "keycloak_url" {
  description = "URL of the shared Keycloak instance"
  value       = "https://${var.keycloak_hostname}/auth"
}

output "keycloak_admin_console" {
  description = "URL of the Keycloak admin console"
  value       = "https://${var.keycloak_hostname}/auth/admin"
}

output "namespace" {
  description = "Kubernetes namespace where Keycloak is deployed"
  value       = kubernetes_namespace.shared_auth.metadata[0].name
}

output "keycloak_internal_url" {
  description = "Internal cluster URL for Keycloak (for branch deployments)"
  value       = "http://keycloak.${var.namespace}.svc.cluster.local:80"
}

output "realm_template_configmap" {
  description = "Name of the ConfigMap containing the realm template"
  value       = kubernetes_config_map.realm_template.metadata[0].name
}

# -----------------------------------------------------------------------------
# Configuration values for branch deployments
# These can be used to configure openhands chart keycloak settings
# -----------------------------------------------------------------------------

output "branch_deployment_config" {
  description = "Configuration values for branch deployments to use shared Keycloak"
  value = {
    keycloak_enabled      = false
    keycloak_external     = true
    keycloak_url          = "https://${var.keycloak_hostname}/auth"
    keycloak_internal_url = "http://keycloak.${var.namespace}.svc.cluster.local:80/auth"
    realm                 = var.realm_name
    client_id             = var.client_id
  }
}
