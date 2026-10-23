output "role_arn" {
  description = "ARN of the deployer role; pass to STS AssumeRole or use as the assume_role target in a provider block."
  value       = aws_iam_role.this.arn
}

output "role_name" {
  description = "Name of the deployer role."
  value       = aws_iam_role.this.name
}

output "policy_arn" {
  description = "ARN of the inline-style managed policy attached to the role."
  value       = aws_iam_policy.deployer.arn
}
