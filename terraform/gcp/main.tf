locals {
  preview_tag = "enterprise-replicated-preview"
  labels = merge(
    var.labels,
    {
      managed-by = "terraform"
      component  = "replicated-preview"
    }
  )
}

data "google_compute_image" "ubuntu" {
  family  = "ubuntu-2404-lts-amd64"
  project = "ubuntu-os-cloud"
}

resource "tls_private_key" "ssh" {
  algorithm = "ED25519"
}

resource "local_sensitive_file" "ssh_private_key" {
  content         = tls_private_key.ssh.private_key_openssh
  filename        = "${path.module}/ssh/${var.instance_name}.pem"
  file_permission = "0600"
}

resource "google_compute_address" "instance" {
  name         = "${var.instance_name}-ip"
  project      = var.project_id
  region       = var.region
  address_type = "EXTERNAL"
  labels       = local.labels
}

resource "google_compute_instance" "openhands" {
  name         = var.instance_name
  project      = var.project_id
  zone         = var.zone
  machine_type = var.machine_type
  tags         = [local.preview_tag, var.instance_name]
  labels       = local.labels

  boot_disk {
    initialize_params {
      image = data.google_compute_image.ubuntu.self_link
      size  = var.boot_disk_size_gb
      type  = "pd-balanced"
    }
  }

  network_interface {
    network    = var.network
    subnetwork = var.subnetwork

    access_config {
      nat_ip = google_compute_address.instance.address
    }
  }

  metadata = {
    ssh-keys = "ubuntu:${tls_private_key.ssh.public_key_openssh}"
  }

  metadata_startup_script = <<-SCRIPT
    #!/usr/bin/env bash
    set -e
    apt-get update
    apt-get install -y curl wget jq tar gzip ca-certificates
    wget -q https://github.com/derailed/k9s/releases/latest/download/k9s_linux_amd64.deb -O /tmp/k9s.deb
    apt-get install -y /tmp/k9s.deb
    rm /tmp/k9s.deb
  SCRIPT

  service_account {
    scopes = ["cloud-platform"]
  }
}

resource "google_compute_firewall" "web" {
  name    = "${var.instance_name}-web"
  project = var.project_id
  network = var.network

  direction     = "INGRESS"
  source_ranges = ["0.0.0.0/0"]
  target_tags   = [local.preview_tag, var.instance_name]

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }
}

resource "google_compute_firewall" "admin" {
  name    = "${var.instance_name}-admin"
  project = var.project_id
  network = var.network

  direction     = "INGRESS"
  source_ranges = var.allowed_admin_cidrs
  target_tags   = [local.preview_tag, var.instance_name]

  allow {
    protocol = "tcp"
    ports    = ["22", "30000"]
  }
}
