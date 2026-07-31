# -----------------------------------------------------------------------------
# Let's Encrypt certificate (when provision_cert = true)
# -----------------------------------------------------------------------------

resource "tls_private_key" "acme_account" {
  count     = var.provision_cert ? 1 : 0
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "acme_registration" "reg" {
  count           = var.provision_cert ? 1 : 0
  account_key_pem = tls_private_key.acme_account[0].private_key_pem
  email_address   = var.acme_email
}

resource "acme_certificate" "cert" {
  count = var.provision_cert ? 1 : 0

  account_key_pem = acme_registration.reg[0].account_key_pem
  common_name     = var.base_domain

  # The wildcard layout collapses to a single SAN: every service hostname and
  # every {id}-runtime sandbox sits one label under base_domain.
  subject_alternative_names = var.hostname_mode == "wildcard" ? [
    "*.${var.base_domain}",
    ] : [
    "app.${var.base_domain}",
    "analytics.app.${var.base_domain}",
    "auth.app.${var.base_domain}",
    "llm-proxy.${var.base_domain}",
    "runtime-api.${var.base_domain}",
    "*.runtime.${var.base_domain}",
  ]

  dns_challenge {
    provider = "route53"
    config = {
      AWS_HOSTED_ZONE_ID = var.route53_zone_id
    }
  }

  depends_on = [aws_route53_record.records]
}

# -----------------------------------------------------------------------------
# User-provided certificate (when provision_cert = false)
# -----------------------------------------------------------------------------

data "local_file" "user_cert" {
  count    = var.provision_cert ? 0 : 1
  filename = var.user_cert_path
}

data "local_sensitive_file" "user_key" {
  count    = var.provision_cert ? 0 : 1
  filename = var.user_private_key_path
}

data "local_file" "user_ca" {
  count    = var.provision_cert ? 0 : 1
  filename = var.user_ca_path
}

# -----------------------------------------------------------------------------
# Unified locals — both paths converge here
# -----------------------------------------------------------------------------

locals {
  certificate_pem = var.provision_cert ? "${acme_certificate.cert[0].certificate_pem}${acme_certificate.cert[0].issuer_pem}" : data.local_file.user_cert[0].content
  private_key_pem = var.provision_cert ? acme_certificate.cert[0].private_key_pem : data.local_sensitive_file.user_key[0].content
  ca_pem          = var.provision_cert ? acme_certificate.cert[0].issuer_pem : data.local_file.user_ca[0].content
}
