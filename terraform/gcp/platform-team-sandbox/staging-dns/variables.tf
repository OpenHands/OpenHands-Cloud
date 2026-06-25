# -----------------------------------------------------------------------------
# Variables for OpenHands Enterprise Staging DNS
# -----------------------------------------------------------------------------

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "parent_zone_name" {
  description = "Name of the parent DNS zone (platform-team-all-hands-dot-dev)"
  type        = string
  default     = "platform-team-all-hands-dot-dev"
}

variable "traefik_lb_ip" {
  description = "IP address of the Traefik LoadBalancer"
  type        = string
}
