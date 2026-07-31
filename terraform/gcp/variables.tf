variable "project_id" {
  description = "GCP project that hosts the preview VM and DNS records."
  type        = string
}

variable "region" {
  description = "GCP region for regional resources such as static IPs."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone for the preview VM."
  type        = string
  default     = "us-central1-a"
}

variable "instance_name" {
  description = "Compute Engine instance name."
  type        = string
}

variable "machine_type" {
  description = "Compute Engine machine type for the Replicated Embedded Cluster VM."
  type        = string
  default     = "c3d-standard-8"
}

variable "boot_disk_size_gb" {
  description = "Boot disk size in GB."
  type        = number
  default     = 200
}

variable "network" {
  description = "VPC network name or self link. In staging this is usually staging-core-app."
  type        = string
}

variable "subnetwork" {
  description = "Subnetwork name or self link. In staging this is usually staging-core-app."
  type        = string
}

variable "base_domain" {
  description = "Base domain for the Replicated install, e.g. replicated-pr-92.staging.all-hands.dev."
  type        = string
}

variable "dns_managed_zone" {
  description = "Cloud DNS managed zone name that contains base_domain."
  type        = string
}

variable "dns_ttl" {
  description = "TTL in seconds for preview DNS A records."
  type        = number
  default     = 300
}

variable "allowed_admin_cidrs" {
  description = "CIDR ranges allowed to reach SSH and the Replicated admin console."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "acme_email" {
  description = "Email address for ACME registration."
  type        = string
}

variable "acme_server" {
  description = "ACME directory URL."
  type        = string
  default     = "https://acme-v02.api.letsencrypt.org/directory"
}

variable "labels" {
  description = "Labels applied to GCP resources. Keys must satisfy GCP label rules."
  type        = map(string)
  default     = {}
}
