output "instance_public_ip" {
  description = "External IP address of the Compute Engine instance."
  value       = google_compute_address.instance.address
}

output "instance_id" {
  description = "Compute Engine instance ID."
  value       = google_compute_instance.openhands.instance_id
}

output "ssh_key_file" {
  description = "Path to the generated SSH private key."
  value       = local_sensitive_file.ssh_private_key.filename
}

output "admin_console_url" {
  description = "Replicated Admin Console URL."
  value       = "https://${var.base_domain}:30000"
}

output "app_url" {
  description = "OpenHands application URL."
  value       = "https://app.${var.base_domain}"
}

output "base_url" {
  description = "OpenHands base URL."
  value       = "https://${var.base_domain}"
}

output "certificate_file" {
  description = "Path to the generated certificate PEM."
  value       = local_file.certificate_pem.filename
}

output "private_key_file" {
  description = "Path to the generated private key PEM."
  value       = local_sensitive_file.private_key_pem.filename
}

output "ca_file" {
  description = "Path to the CA certificate PEM."
  value       = local_file.ca_pem.filename
}
