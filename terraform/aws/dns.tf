# -----------------------------------------------------------------------------
# Route 53 A Records
# -----------------------------------------------------------------------------

locals {
  # On the wildcard layout every hostname -- app, auth, analytics, llm-proxy,
  # runtime-api and each {id}-runtimes sandbox -- is a single label under
  # base_domain, so one wildcard record covers all of them. The legacy layout
  # needs each name called out, plus its own sandbox wildcard a level deeper.
  dns_records = var.hostname_mode == "wildcard" ? {
    base     = var.base_domain
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
