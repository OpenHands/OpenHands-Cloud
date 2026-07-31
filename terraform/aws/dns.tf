# -----------------------------------------------------------------------------
# Route 53 A Records
# -----------------------------------------------------------------------------

locals {
  # On the wildcard layout every hostname -- app, auth, analytics, llm-proxy,
  # runtime-api, the admin console at admin., and each {id}-runtime sandbox --
  # is a single label under base_domain, so one wildcard record covers all of
  # them. Nothing answers at the apex, and a wildcard matches exactly one label
  # rather than the domain itself, so the apex gets no record. The legacy layout
  # needs each name called out, plus its own sandbox wildcard a level deeper.
  dns_records = var.hostname_mode == "wildcard" ? {
    wildcard = "*.${var.base_domain}"
    } : {
    base         = var.base_domain
    app          = "app.${var.base_domain}"
    analytics    = "analytics.app.${var.base_domain}"
    auth         = "auth.app.${var.base_domain}"
    llm_proxy    = "llm-proxy.${var.base_domain}"
    runtime_api  = "runtime-api.${var.base_domain}"
    runtime_wild = "*.runtime.${var.base_domain}"
  }
}

resource "aws_route53_record" "records" {
  for_each = var.route53_zone_id != "" ? local.dns_records : {}

  zone_id = var.route53_zone_id
  name    = each.value
  type    = "A"
  ttl     = var.dns_ttl
  records = [aws_eip.instance.public_ip]
}
