resource "random_pet" "subdomain" {
  length    = 2
  separator = "-"

  # Stable across applies; regenerate only if we point at a different zone.
  # `zone_name` is in the keeper too so a zone migration that keeps the same
  # zone_id (rare but possible across providers) still triggers regeneration.
  keepers = {
    zone_id   = var.zone_id
    zone_name = var.zone_name
  }
}

locals {
  fqdn = "${random_pet.subdomain.id}.${var.zone_name}"
}

resource "aws_acm_certificate" "this" {
  domain_name       = local.fqdn
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${var.name}-${random_pet.subdomain.id}"
  }
}

resource "aws_route53_record" "cert_validation" {
  allow_overwrite = true
  zone_id         = var.zone_id
  name            = one(aws_acm_certificate.this.domain_validation_options).resource_record_name
  type            = one(aws_acm_certificate.this.domain_validation_options).resource_record_type
  records         = [one(aws_acm_certificate.this.domain_validation_options).resource_record_value]
  ttl             = 60
}

resource "aws_acm_certificate_validation" "this" {
  certificate_arn         = aws_acm_certificate.this.arn
  validation_record_fqdns = [aws_route53_record.cert_validation.fqdn]
}

resource "aws_route53_record" "alias" {
  zone_id = var.zone_id
  name    = local.fqdn
  type    = "A"

  alias {
    name                   = aws_lb.this.dns_name
    zone_id                = aws_lb.this.zone_id
    evaluate_target_health = true
  }
}
