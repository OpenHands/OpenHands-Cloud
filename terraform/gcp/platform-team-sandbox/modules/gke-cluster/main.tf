# -----------------------------------------------------------------------------
# GKE Cluster Module
# -----------------------------------------------------------------------------

resource "google_container_cluster" "cluster" {
  name     = var.cluster_name
  project  = var.project_id
  location = var.location

  # Use regional cluster for HA, or zonal for cost savings
  node_locations = var.node_locations

  network    = var.network_name
  subnetwork = var.subnet_name

  # Enable Autopilot mode if specified (only set if true to avoid conflicts)
  enable_autopilot = var.enable_autopilot ? true : null

  # For standard (non-Autopilot) clusters: manage node pools separately
  remove_default_node_pool = var.enable_autopilot ? null : true
  initial_node_count       = var.enable_autopilot ? null : 1

  # IP allocation policy for VPC-native cluster
  ip_allocation_policy {
    cluster_secondary_range_name  = var.pods_range_name
    services_secondary_range_name = var.services_range_name
  }

  # Private cluster configuration
  dynamic "private_cluster_config" {
    for_each = var.enable_private_nodes ? [1] : []
    content {
      enable_private_nodes    = true
      enable_private_endpoint = var.enable_private_endpoint
      master_ipv4_cidr_block  = var.master_ipv4_cidr_block
    }
  }

  # Master authorized networks
  dynamic "master_authorized_networks_config" {
    for_each = length(var.master_authorized_networks) > 0 ? [1] : []
    content {
      dynamic "cidr_blocks" {
        for_each = var.master_authorized_networks
        content {
          cidr_block   = cidr_blocks.value.cidr_block
          display_name = cidr_blocks.value.display_name
        }
      }
    }
  }

  # Workload Identity
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # Release channel
  release_channel {
    channel = var.release_channel
  }

  # Addons - only for standard clusters (Autopilot manages these automatically)
  dynamic "addons_config" {
    for_each = var.enable_autopilot ? [] : [1]
    content {
      http_load_balancing {
        disabled = !var.enable_http_load_balancing
      }
      horizontal_pod_autoscaling {
        disabled = false
      }
    }
  }

  # Network policy - only for standard clusters
  dynamic "network_policy" {
    for_each = var.enable_network_policy && !var.enable_autopilot ? [1] : []
    content {
      enabled  = true
      provider = "CALICO"
    }
  }

  # Maintenance window
  maintenance_policy {
    daily_maintenance_window {
      start_time = var.maintenance_start_time
    }
  }

  # Labels
  resource_labels = var.labels

  # Deletion protection
  deletion_protection = var.deletion_protection

  lifecycle {
    ignore_changes = [
      node_config,
      initial_node_count,
      node_locations,  # GKE auto-manages this based on regional availability
    ]
  }
}

# -----------------------------------------------------------------------------
# Node Pool (only for non-Autopilot clusters)
# -----------------------------------------------------------------------------

resource "google_container_node_pool" "primary" {
  count = var.enable_autopilot ? 0 : 1

  name     = "${var.cluster_name}-primary"
  project  = var.project_id
  location = var.location
  cluster  = google_container_cluster.cluster.name

  initial_node_count = var.node_pool_initial_count

  autoscaling {
    min_node_count = var.node_pool_min_count
    max_node_count = var.node_pool_max_count
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type = var.node_machine_type
    disk_size_gb = var.node_disk_size_gb
    disk_type    = var.node_disk_type

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform",
    ]

    labels = var.node_labels

    # Workload Identity
    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    tags = var.node_tags
  }

  lifecycle {
    ignore_changes = [
      initial_node_count,
    ]
  }
}

# -----------------------------------------------------------------------------
# Runtime Node Pool (optional, for dedicated runtime nodes)
# -----------------------------------------------------------------------------
# This node pool is designed to run OpenHands runtime containers.
# Supports two isolation modes:
#
# 1. GKE Sandbox (gVisor) - enable_gke_sandbox = true
#    - Native GKE sandbox feature using gVisor
#    - No additional installation required
#    - Better security isolation
#
# 2. Sysbox - enable_gke_sandbox = false
#    - Uses Sysbox for nested container support
#    - Requires sysbox DaemonSet installation
#    - Labeled with sysbox-install=yes
#
# Key features:
# - Tainted to prevent non-runtime workloads from scheduling
# - Larger disk for runtime image caching (~10GB+ runtime images)
# -----------------------------------------------------------------------------

resource "google_container_node_pool" "runtime" {
  count = var.enable_autopilot || !var.create_runtime_node_pool ? 0 : 1

  name     = "${var.cluster_name}-runtime"
  project  = var.project_id
  location = var.location
  cluster  = google_container_cluster.cluster.name

  initial_node_count = var.runtime_node_pool_initial_count

  autoscaling {
    min_node_count = var.runtime_node_pool_min_count
    max_node_count = var.runtime_node_pool_max_count
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type = var.runtime_node_machine_type
    disk_size_gb = var.runtime_node_disk_size_gb
    disk_type    = var.node_disk_type

    # Image type: Use UBUNTU_CONTAINERD for sysbox support
    # Sysbox requires Ubuntu (Container-Optimized OS is not supported)
    image_type = var.enable_gke_sandbox ? "COS_CONTAINERD" : var.runtime_node_image_type

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform",
    ]

    # Labels for node selection
    # - For gVisor: GKE automatically adds sandbox.gke.io/runtime=gvisor
    # - For sysbox: sysbox-install=yes used by sysbox DaemonSet and image-loader
    # - openhands.ai/node-type: General classification label
    labels = merge(
      var.node_labels,
      {
        "openhands.ai/node-type" = "runtime"
      },
      # Only add sysbox-install label when NOT using GKE Sandbox
      var.enable_gke_sandbox ? {} : {
        "sysbox-install" = "yes"
      }
    )

    # Taint to prevent non-runtime workloads from scheduling on these nodes
    # For gVisor: GKE auto-applies sandbox.gke.io/runtime=gvisor taint (must NOT be specified manually)
    # For sysbox: sysbox-runtime=true taint applied here
    dynamic "taint" {
      for_each = var.enable_gke_sandbox ? [] : [1]
      content {
        key    = "sysbox-runtime"
        value  = "true"
        effect = "NO_SCHEDULE"
      }
    }

    # GKE Sandbox (gVisor) configuration
    dynamic "sandbox_config" {
      for_each = var.enable_gke_sandbox ? [1] : []
      content {
        type = "GVISOR"  # Must be uppercase per Google provider 7.29.0+
      }
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    tags = var.node_tags
  }

  lifecycle {
    ignore_changes = [
      initial_node_count,
    ]
  }
}
