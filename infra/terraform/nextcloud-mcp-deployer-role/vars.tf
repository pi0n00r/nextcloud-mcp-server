variable "role_name" {
  description = "Name of the deployer IAM role."
  type        = string
  default     = "nextcloud-mcp-deployer"
}

variable "role_path" {
  description = "IAM path for the deployer role and its policy."
  type        = string
  default     = "/clients/"
}

variable "trusted_principal_arns" {
  description = <<-EOT
    Principal ARNs allowed to assume this role. For testing in your own
    account: the user/role you want to assume from. For client deployments:
    typically a single root-account ARN of the deploying party (e.g.
    "arn:aws:iam::<your-account-id>:root"), with MFA or external-id
    conditions added at the trust-policy level if required.
  EOT
  type        = list(string)
  validation {
    condition     = length(var.trusted_principal_arns) > 0
    error_message = "trusted_principal_arns must contain at least one ARN."
  }
}

variable "module_name_prefix" {
  description = <<-EOT
    The `var.name` value passed to the nextcloud-mcp-server module. Used to
    scope IAM/logs/secrets ARNs. Defaults match the module default; change
    only if the module is instantiated with a non-default name.
  EOT
  type        = string
  default     = "nextcloud-mcp-server"
}

variable "secret_name_prefix" {
  description = <<-EOT
    Secrets Manager name prefix the deployer can read (and optionally
    create, see `allow_secret_create`). The module accepts a secret ARN as
    input; this prefix scopes the deployer's access to secrets matching
    that name pattern.
  EOT
  type        = string
  default     = "nextcloud-mcp"
}

variable "allow_secret_create" {
  description = <<-EOT
    When true, the deployer can create/update/delete Secrets Manager
    secrets matching `secret_name_prefix`. Set true if the secret is
    managed alongside the module in the same Terraform run; leave false if
    the secret is provisioned out of band (console / separate root TF) and
    only the ARN is passed in.
  EOT
  type        = bool
  default     = false
}

variable "route53_zone_ids" {
  description = <<-EOT
    Route53 public hosted zone IDs the deployer is allowed to mutate. The
    server module always creates Route53 records (ALB alias + ACM DNS-01
    validation), so this should be set to the zone(s) the module's
    `zone_id` input points at. Leaving it empty falls back to `*` as a
    convenience but is not recommended in production — scope it.
  EOT
  type        = list(string)
  default     = []
}
