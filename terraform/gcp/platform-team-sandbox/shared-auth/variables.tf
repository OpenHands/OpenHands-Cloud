# -----------------------------------------------------------------------------
# Variables for Shared Keycloak Infrastructure
# -----------------------------------------------------------------------------

variable "kube_context" {
  description = "Kubernetes context to use (from kubeconfig)"
  type        = string
  default     = "gke_platform-team-sandbox-62793_us-central1_ohe-staging-path-cluster"
}

variable "namespace" {
  description = "Kubernetes namespace for shared auth services"
  type        = string
  default     = "shared-auth"
}

variable "keycloak_hostname" {
  description = "Hostname for Keycloak ingress"
  type        = string
  default     = "auth.ohe-staging.platform-team.all-hands.dev"
}

variable "keycloak_admin_password" {
  description = "Admin password for Keycloak"
  type        = string
  sensitive   = true
}

variable "keycloak_chart_version" {
  description = "Version of the codecentric/keycloakx Helm chart"
  type        = string
  default     = "7.1.11"
}

# -----------------------------------------------------------------------------
# Realm and Client Configuration
# -----------------------------------------------------------------------------

variable "realm_name" {
  description = "Name of the Keycloak realm for staging (must be 'allhands' to match OpenHands)"
  type        = string
  default     = "allhands"
}

variable "client_id" {
  description = "Client ID for the OpenHands staging client (must be 'allhands' to match OpenHands)"
  type        = string
  default     = "allhands"
}

variable "client_secret" {
  description = "Client secret for the OpenHands staging client"
  type        = string
  sensitive   = true
}

# -----------------------------------------------------------------------------
# PostgreSQL Configuration
# Uses the shared PostgreSQL instance from openhands namespace
# -----------------------------------------------------------------------------

variable "postgres_host" {
  description = "PostgreSQL host (can be cross-namespace service DNS)"
  type        = string
  default     = "openhands-postgresql.openhands.svc.cluster.local"
}

variable "postgres_database" {
  description = "PostgreSQL database name for Keycloak"
  type        = string
  default     = "shared_keycloak"
}

variable "postgres_username" {
  description = "PostgreSQL username"
  type        = string
  default     = "postgres"
}

variable "postgres_password" {
  description = "PostgreSQL password"
  type        = string
  sensitive   = true
}

# -----------------------------------------------------------------------------
# Ingress Configuration
# -----------------------------------------------------------------------------

variable "ingress_class" {
  description = "Ingress class name"
  type        = string
  default     = "traefik"
}

variable "tls_secret_name" {
  description = "Name of the TLS secret for Keycloak ingress"
  type        = string
  default     = "ohe-staging-wildcard-tls"
}

# -----------------------------------------------------------------------------
# Enterprise SSO (SAML) Configuration
# For Google Workspace SSO integration
# -----------------------------------------------------------------------------

variable "saml_sso_url" {
  description = "SAML Single Sign-On Service URL from Google Workspace Admin Console"
  type        = string
  default     = ""
}

variable "saml_signing_certificate" {
  description = "SAML signing certificate (X.509 PEM format, without headers) from Google Workspace"
  type        = string
  default     = ""
  sensitive   = true
}
