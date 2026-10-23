output "url" {
  description = "Public HTTPS URL of the MCP server"
  value       = "https://${local.fqdn}"
}

output "subdomain" {
  description = "Generated random subdomain (label only, without the zone)"
  value       = random_pet.subdomain.id
}

output "fqdn" {
  description = "Fully-qualified domain name"
  value       = local.fqdn
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "ecs_service_name" {
  value = aws_ecs_service.this.name
}

output "efs_id" {
  description = "EFS file-system ID. Marked sensitive — surfacing it in CI logs invites enumeration of mount targets."
  value       = aws_efs_file_system.this.id
  sensitive   = true
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.this.name
}

output "task_role_arn" {
  description = "Task role ARN. Marked sensitive — knowing the ARN is the first step to abusing it via SSRF/role-confusion."
  value       = aws_iam_role.task.arn
  sensitive   = true
}

output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "qdrant_service_name" {
  description = "Qdrant ECS service name (null when use_external_qdrant = true)"
  value       = var.use_external_qdrant ? null : aws_ecs_service.qdrant[0].name
}

output "qdrant_dns_name" {
  description = "Internal DNS name where mcp-server reaches qdrant (null when use_external_qdrant = true)."
  value       = var.use_external_qdrant ? null : "qdrant.${aws_service_discovery_private_dns_namespace.this.name}"
}
