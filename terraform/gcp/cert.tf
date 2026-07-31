provider "acme" {
  server_url = var.acme_server
}

resource "tls_private_key" "acme_account" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "acme_registration" "reg" {
  account_key_pem = tls_private_key.acme_account.private_key_pem
  email_address   = var.acme_email
}

resource "acme_certificate" "cert" {
  account_key_pem = acme_registration.reg.account_key_pem
  common_name     = var.base_domain

  subject_alternative_names = [
    "app.${var.base_domain}",
    "analytics.app.${var.base_domain}",
    "auth.app.${var.base_domain}",
    "llm-proxy.${var.base_domain}",
    "runtime-api.${var.base_domain}",
    "*.runtime.${var.base_domain}",
  ]

  dns_challenge {
    provider = "gcloud"
    config = {
      GCE_PROJECT = var.project_id
    }
  }

  depends_on = [google_dns_record_set.records]
}

locals {
  certificate_pem = "${acme_certificate.cert.certificate_pem}${acme_certificate.cert.issuer_pem}"
  private_key_pem = acme_certificate.cert.private_key_pem
  ca_pem          = acme_certificate.cert.issuer_pem
}

resource "local_file" "certificate_pem" {
  content  = local.certificate_pem
  filename = "${path.module}/certs/${var.instance_name}.crt"
}

resource "local_sensitive_file" "private_key_pem" {
  content         = local.private_key_pem
  filename        = "${path.module}/certs/${var.instance_name}.key"
  file_permission = "0600"
}

resource "local_file" "ca_pem" {
  content  = local.ca_pem
  filename = "${path.module}/certs/${var.instance_name}-ca.crt"
}
