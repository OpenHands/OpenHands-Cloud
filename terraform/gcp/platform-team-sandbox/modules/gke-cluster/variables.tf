# -----------------------------------------------------------------------------
# Project & Location
# -----------------------------------------------------------------------------

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "location" {
  description = "GKE cluster location (region for regional cluster, zone for zonal)"
  type        = string
}

variable "node_locations" {
  description = "List of zones for node placement (leave empty for single-zone)"
  type        = list(string)
  default     = []
}

# -----------------------------------------------------------------------------
# Cluster Configuration
# -----------------------------------------------------------------------------

variable "cluster_name" {
  description = "Name of the GKE cluster"
  type        = string
}

variable "enable_autopilot" {
  description = "Enable GKE Autopilot mode"
  type        = bool
  default     = false
}

variable "release_channel" {
  description = "GKE release channel (RAPID, REGULAR, STABLE, UNSPECIFIED)"
  type        = string
  default     = "REGULAR"
}

variable "deletion_protection" {
  description = "Enable deletion protection for the cluster"
  type        = bool
  default     = false
}

# -----------------------------------------------------------------------------
# Network Configuration
# -----------------------------------------------------------------------------

variable "network_name" {
  description = "Name of the VPC network"
  type        = string
}

variable "subnet_name" {
  description = "Name of the subnet"
  type        = string
}

variable "pods_range_name" {
  description = "Name of the secondary IP range for pods"
  type        = string
}

variable "services_range_name" {
  description = "Name of the secondary IP range for services"
  type        = string
}

variable "enable_private_nodes" {
  description = "Enable private nodes (no public IPs)"
  type        = bool
  default     = true
}

variable "enable_private_endpoint" {
  description = "Enable private endpoint (master not accessible from public internet)"
  type        = bool
  default     = false
}

variable "master_ipv4_cidr_block" {
  description = "CIDR block for the master's private IP"
  type        = string
  default     = "172.16.0.0/28"
}

variable "master_authorized_networks" {
  description = "List of CIDR blocks authorized to access the master"
  type = list(object({
    cidr_block   = string
    display_name = string
  }))
  default = []
}

# -----------------------------------------------------------------------------
# Cluster Autoscaling
# -----------------------------------------------------------------------------

variable "enable_cluster_autoscaling" {
  description = "Enable cluster-level autoscaling"
  type        = bool
  default     = false
}

variable "autoscaling_resource_limits" {
  description = "Resource limits for cluster autoscaling"
  type = list(object({
    resource_type = string
    minimum       = number
    maximum       = number
  }))
  default = [
    {
      resource_type = "cpu"
      minimum       = 4
      maximum       = 100
    },
    {
      resource_type = "memory"
      minimum       = 16
      maximum       = 400
    }
  ]
}

# -----------------------------------------------------------------------------
# Node Pool Configuration
# -----------------------------------------------------------------------------

variable "node_machine_type" {
  description = "Machine type for nodes"
  type        = string
  default     = "e2-standard-4"
}

variable "node_disk_size_gb" {
  description = "Disk size in GB for nodes"
  type        = number
  default     = 100
}

variable "node_disk_type" {
  description = "Disk type for nodes (pd-standard, pd-ssd, pd-balanced)"
  type        = string
  default     = "pd-balanced"
}

variable "node_pool_initial_count" {
  description = "Initial number of nodes per zone"
  type        = number
  default     = 1
}

variable "node_pool_min_count" {
  description = "Minimum number of nodes per zone"
  type        = number
  default     = 1
}

variable "node_pool_max_count" {
  description = "Maximum number of nodes per zone"
  type        = number
  default     = 5
}

variable "node_labels" {
  description = "Labels to apply to nodes"
  type        = map(string)
  default     = {}
}

variable "node_tags" {
  description = "Network tags to apply to nodes"
  type        = list(string)
  default     = []
}

# -----------------------------------------------------------------------------
# Runtime Node Pool Configuration (optional)
# -----------------------------------------------------------------------------
# The runtime node pool provides isolated execution environments for OpenHands.
# Two isolation options are supported:
#
# 1. GKE Sandbox (gVisor) - enable_gke_sandbox = true
#    - Uses gVisor for kernel-level isolation
#    - Native GKE feature, no additional installation required
#    - Nodes labeled with sandbox.gke.io/runtime=gvisor
#    - Set RUNTIME_CLASS="gvisor" in runtime-api helm values
#
# 2. Sysbox - enable_gke_sandbox = false (default)
#    - Uses Sysbox for nested container isolation
#    - Requires sysbox DaemonSet installation
#    - Nodes labeled with sysbox-install=yes
#    - Set RUNTIME_CLASS="sysbox-runc" in runtime-api helm values
# -----------------------------------------------------------------------------

variable "create_runtime_node_pool" {
  description = "Create a dedicated node pool for runtimes"
  type        = bool
  default     = false
}

variable "enable_gke_sandbox" {
  description = "Enable GKE Sandbox (gVisor) on runtime nodes. When true, uses gVisor for isolation. When false, expects sysbox to be installed."
  type        = bool
  default     = false
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

variable "runtime_node_pool_initial_count" {
  description = "Initial number of runtime nodes per zone"
  type        = number
  default     = 1
}

variable "runtime_node_pool_min_count" {
  description = "Minimum number of runtime nodes per zone"
  type        = number
  default     = 0
}

variable "runtime_node_pool_max_count" {
  description = "Maximum number of runtime nodes per zone"
  type        = number
  default     = 10
}

variable "runtime_node_image_type" {
  description = "Image type for runtime nodes. Use 'UBUNTU_CONTAINERD' for sysbox support (sysbox requires Ubuntu, not COS). Only used when enable_gke_sandbox=false."
  type        = string
  default     = "UBUNTU_CONTAINERD"
}

# -----------------------------------------------------------------------------
# Addons & Features
# -----------------------------------------------------------------------------

variable "enable_http_load_balancing" {
  description = "Enable HTTP load balancing addon"
  type        = bool
  default     = true
}

variable "enable_network_policy" {
  description = "Enable network policy (Calico)"
  type        = bool
  default     = false
}

variable "maintenance_start_time" {
  description = "Start time for daily maintenance window (UTC)"
  type        = string
  default     = "03:00"
}

# -----------------------------------------------------------------------------
# Labels
# -----------------------------------------------------------------------------

variable "labels" {
  description = "Labels to apply to the cluster"
  type        = map(string)
  default     = {}
}
