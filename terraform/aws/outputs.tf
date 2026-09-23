output "instance_public_ip" {
  description = "Elastic IP address of the EC2 instance"
  value       = aws_eip.instance.public_ip
}

output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.openhands.id
}

output "ssh_key_file" {
  description = "Path to the generated SSH private key"
  value       = local_sensitive_file.ssh_private_key.filename
}

output "admin_console_url" {
  description = "Replicated Admin Console URL"
  value       = "https://admin.${var.base_domain}:30000"
}

output "app_url" {
  description = "OpenHands application URL"
  value       = "https://app.${var.base_domain}"
}

output "certificate_file" {
  description = "Path to the generated certificate PEM"
  value       = local_file.certificate_pem.filename
}

output "private_key_file" {
  description = "Path to the generated private key PEM"
  value       = local_sensitive_file.private_key_pem.filename
}

output "ca_file" {
  description = "Path to the CA certificate PEM"
  value       = local_file.ca_pem.filename
}
