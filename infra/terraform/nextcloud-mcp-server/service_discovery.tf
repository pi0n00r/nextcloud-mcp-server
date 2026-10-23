resource "aws_service_discovery_private_dns_namespace" "this" {
  name        = "${var.name}.local"
  description = "Private DNS namespace for ${var.name} internal services"
  vpc         = var.vpc_id
}
