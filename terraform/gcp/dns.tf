locals {
  dns_records = {
    base         = var.base_domain
    app          = "app.${var.base_domain}"
    analytics    = "analytics.app.${var.base_domain}"
    auth         = "auth.app.${var.base_domain}"
    llm_proxy    = "llm-proxy.${var.base_domain}"
    runtime_api  = "runtime-api.${var.base_domain}"
    runtime_wild = "*.runtime.${var.base_domain}"
  }
}

resource "google_dns_record_set" "records" {
  for_each = local.dns_records

  project      = var.project_id
  managed_zone = var.dns_managed_zone
  name         = "${each.value}."
  type         = "A"
  ttl          = var.dns_ttl
  rrdatas      = [google_compute_address.instance.address]
}
