# -----------------------------------------------------------------------------
# Project Configuration
# -----------------------------------------------------------------------------

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for resources"
  type        = string
  default     = "us-central1"
}

variable "environment_name" {
  description = "Name of the environment (used as prefix for all resources)"
  type        = string
  default     = "oh-single-path"
}

# -----------------------------------------------------------------------------
# Network Configuration
# -----------------------------------------------------------------------------

variable "subnet_cidr" {
  description = "CIDR range for the subnet"
  type        = string
  default     = "10.0.0.0/20"
}

variable "pods_cidr" {
  description = "CIDR range for GKE pods"
  type        = string
  default     = "10.48.0.0/14"
}

variable "services_cidr" {
  description = "CIDR range for GKE services"
  type        = string
  default     = "10.52.0.0/20"
}

variable "master_ipv4_cidr_block" {
  description = "CIDR block for the GKE master"
  type        = string
  default     = "172.16.0.0/28"
}

variable "enable_private_nodes" {
  description = "Enable private nodes (no public IPs on nodes)"
  type        = bool
  default     = true
}

variable "master_authorized_networks" {
  description = "CIDR blocks authorized to access the Kubernetes master"
  type = list(object({
    cidr_block   = string
    display_name = string
  }))
  default = [
    {
      cidr_block   = "0.0.0.0/0"
      display_name = "All (update for production)"
    }
  ]
}

# -----------------------------------------------------------------------------
# Cluster Configuration
# -----------------------------------------------------------------------------

variable "enable_autopilot" {
  description = "Enable GKE Autopilot mode (recommended for simplicity)"
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Enable deletion protection for the cluster"
  type        = bool
  default     = false
}

# -----------------------------------------------------------------------------
# Node Pool Configuration (ignored if autopilot enabled)
# -----------------------------------------------------------------------------

variable "node_machine_type" {
  description = "Machine type for primary node pool"
  type        = string
  default     = "e2-standard-4"
}

variable "node_disk_size_gb" {
  description = "Disk size in GB for primary nodes"
  type        = number
  default     = 100
}

variable "node_pool_min_count" {
  description = "Minimum nodes in primary pool"
  type        = number
  default     = 1
}

variable "node_pool_max_count" {
  description = "Maximum nodes in primary pool"
  type        = number
  default     = 5
}

variable "node_pool_initial_count" {
  description = "Initial nodes in primary pool"
  type        = number
  default     = 2
}

# -----------------------------------------------------------------------------
# Runtime Node Pool Configuration
# -----------------------------------------------------------------------------

variable "create_runtime_node_pool" {
  description = "Create dedicated node pool for runtimes"
  type        = bool
  default     = true
}

variable "enable_gke_sandbox" {
  description = "Enable GKE Sandbox (gVisor) on runtime nodes. Set RUNTIME_CLASS='gvisor' in helm values when true."
  type        = bool
  default     = true
}

variable "runtime_node_machine_type" {
  description = "Machine type for runtime nodes"
  type        = string
  default     = "e2-standard-8"
}

variable "runtime_node_disk_size_gb" {
  description = "Disk size in GB for runtime nodes"
  type        = number
  default     = 200
}

variable "runtime_node_pool_min_count" {
  description = "Minimum runtime nodes"
  type        = number
  default     = 0
}

variable "runtime_node_pool_max_count" {
  description = "Maximum runtime nodes"
  type        = number
  default     = 10
}

variable "runtime_node_pool_initial_count" {
  description = "Initial runtime nodes"
  type        = number
  default     = 1
}

# -----------------------------------------------------------------------------
# Domain Configuration
# -----------------------------------------------------------------------------

variable "domain" {
  description = "Domain for the environment (e.g., single-path.openhands-dev.com)"
  type        = string
}

variable "create_dns_zone" {
  description = "Create a Cloud DNS zone for the domain"
  type        = bool
  default     = false
}

# -----------------------------------------------------------------------------
# Labels
# -----------------------------------------------------------------------------

variable "labels" {
  description = "Labels to apply to all resources"
  type        = map(string)
  default     = {}
}
