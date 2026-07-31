project_id       = "staging-092324"
region           = "us-central1"
zone             = "us-central1-a"
instance_name    = "oh-ent-pr-92-c23c797"
base_domain      = "pr-92.replicated.staging.all-hands.dev"
network          = "staging-core-app"
subnetwork       = "staging-core-app"
dns_managed_zone = "staging-all-hands-dot-dev"
acme_email       = "ops@example.com"

machine_type      = "c3d-standard-8"
boot_disk_size_gb = 200
allowed_admin_cidrs = [
  "203.0.113.4/32",
]

labels = {
  environment   = "preview"
  preview-kind  = "enterprise-replicated"
  enterprise-pr = "92"
}
