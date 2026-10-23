terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  prefix     = var.module_name_prefix
}

###
# Role + trust

resource "aws_iam_role" "this" {
  name               = var.role_name
  path               = var.role_path
  assume_role_policy = data.aws_iam_policy_document.trust.json
  description        = "Deploy + manage the nextcloud-mcp-server Terraform module."
}

data "aws_iam_policy_document" "trust" {
  statement {
    sid     = "AllowTrustedPrincipals"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = var.trusted_principal_arns
    }
  }
}

###
# Deployer policy
#
# Scoping strategy: actions that have no usable resource-ARN form at create
# time (RegisterTaskDefinition, CreateLoadBalancer, CreateFileSystem, all of
# CloudFront, etc.) are granted on `*` — IAM gives no other option there.
# Where ARN scoping IS available and worth the surface reduction (IAM, logs,
# secrets, route53), the policy is scoped tight to the module's name prefix.

data "aws_iam_policy_document" "deployer" {

  # --- Compute / runtime: ECS, ALB, EFS ---
  #
  # All three services are gated by SG/SCP/account boundaries already, and
  # most of their create-time APIs reject resource-ARN scoping. Granting
  # service-wide is the standard pattern for a deployer role.
  statement {
    sid       = "EcsService"
    actions   = ["ecs:*"]
    resources = ["*"]
  }

  statement {
    sid       = "ElbV2Service"
    actions   = ["elasticloadbalancing:*"]
    resources = ["*"]
  }

  statement {
    sid       = "EfsService"
    actions   = ["elasticfilesystem:*"]
    resources = ["*"]
  }

  # --- Service Discovery / Cloud Map ---
  #
  # Module always creates `aws_service_discovery_private_dns_namespace` (in
  # service_discovery.tf) and `aws_service_discovery_service` (in qdrant.tf).
  # Cloud Map APIs don't accept resource-ARN scoping at create time; the
  # blast radius is bounded by the SG/account boundary already.
  statement {
    sid = "ServiceDiscoveryService"
    actions = [
      "servicediscovery:CreatePrivateDnsNamespace",
      "servicediscovery:DeleteNamespace",
      "servicediscovery:GetNamespace",
      "servicediscovery:ListNamespaces",
      "servicediscovery:GetOperation",
      "servicediscovery:CreateService",
      "servicediscovery:DeleteService",
      "servicediscovery:GetService",
      "servicediscovery:UpdateService",
      "servicediscovery:ListServices",
      "servicediscovery:TagResource",
      "servicediscovery:UntagResource",
      "servicediscovery:ListTagsForResource",
    ]
    resources = ["*"]
  }

  # --- Logs ---
  #
  # Module creates a single log group: `/ecs/${var.name}`. Scope CW Logs
  # writes/reads to that prefix; describe APIs need `*` because they don't
  # accept resource-ARN scoping.
  statement {
    sid     = "LogsManageGroup"
    actions = ["logs:*"]
    resources = [
      "arn:${local.partition}:logs:*:${local.account_id}:log-group:/ecs/${local.prefix}*",
      "arn:${local.partition}:logs:*:${local.account_id}:log-group:/ecs/${local.prefix}*:*",
      "arn:${local.partition}:logs:*:${local.account_id}:log-group:/ecs/${local.prefix}*:log-stream:*",
    ]
  }

  statement {
    sid = "LogsDescribe"
    actions = [
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
    ]
    resources = ["*"]
  }

  # --- IAM ---
  #
  # The module creates two roles under path `/ecs/`: `${name}-execution` and
  # `${name}-task`, plus inline policies on each. Scope to that path+prefix
  # so the deployer can't pivot to creating arbitrary roles.
  statement {
    sid = "IamManageModuleRoles"
    actions = [
      "iam:CreateRole",
      "iam:GetRole",
      "iam:DeleteRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:PutRolePolicy",
      "iam:GetRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:ListRoleTags",
    ]
    resources = [
      "arn:${local.partition}:iam::${local.account_id}:role/ecs/${local.prefix}-*",
    ]
  }

  # ECS RunTask + service updates need iam:PassRole on the task/exec roles.
  statement {
    sid     = "IamPassModuleRoles"
    actions = ["iam:PassRole"]
    resources = [
      "arn:${local.partition}:iam::${local.account_id}:role/ecs/${local.prefix}-*",
    ]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  # The execution role attaches the AWS-managed AmazonECSTaskExecutionRolePolicy.
  # Restrict the Attach/Detach actions to that single managed policy ARN so
  # this grant can't be used to attach AdministratorAccess or similar.
  statement {
    sid = "IamAttachManagedTaskExecPolicy"
    actions = [
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
    ]
    resources = [
      "arn:${local.partition}:iam::${local.account_id}:role/ecs/${local.prefix}-*",
    ]
    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values = [
        "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
      ]
    }
  }

  # --- Secrets Manager ---
  #
  # The module reads a caller-supplied secret ARN at deploy time
  # (execution-role policy in iam.tf:26). Optionally allow create/update
  # for callers who manage the secret in their own TF.
  statement {
    sid = "SecretsRead"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
      "secretsmanager:ListSecrets",
    ]
    resources = [
      "arn:${local.partition}:secretsmanager:*:${local.account_id}:secret:${var.secret_name_prefix}*",
    ]
  }

  dynamic "statement" {
    for_each = var.allow_secret_create ? [1] : []
    content {
      sid = "SecretsManage"
      actions = [
        "secretsmanager:CreateSecret",
        "secretsmanager:UpdateSecret",
        "secretsmanager:DeleteSecret",
        "secretsmanager:PutSecretValue",
        "secretsmanager:TagResource",
        "secretsmanager:UntagResource",
        "secretsmanager:GetResourcePolicy",
        "secretsmanager:PutResourcePolicy",
      ]
      resources = [
        "arn:${local.partition}:secretsmanager:*:${local.account_id}:secret:${var.secret_name_prefix}*",
      ]
    }
  }

  # --- EC2: Security Groups + describe-only networking ---
  #
  # SG mutations have no usable resource-ARN scoping at create time
  # (CreateSecurityGroup returns the ID), so SG actions are granted on `*`.
  # The describe set is needed for the module's data sources and SG-rule
  # references (managed prefix list lookup for the CloudFront SG lock).
  statement {
    sid = "Ec2NetworkDescribe"
    actions = [
      "ec2:DescribeVpcs",
      "ec2:DescribeSubnets",
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeSecurityGroupRules",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DescribeRouteTables",
      "ec2:DescribePrefixLists",
      "ec2:DescribeManagedPrefixLists",
      "ec2:GetManagedPrefixListEntries",
      "ec2:DescribeTags",
    ]
    resources = ["*"]
  }

  statement {
    sid = "Ec2SecurityGroupManage"
    actions = [
      "ec2:CreateSecurityGroup",
      "ec2:DeleteSecurityGroup",
      "ec2:ModifySecurityGroupRules",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:UpdateSecurityGroupRuleDescriptionsIngress",
      "ec2:UpdateSecurityGroupRuleDescriptionsEgress",
      "ec2:CreateTags",
      "ec2:DeleteTags",
    ]
    resources = ["*"]
  }

  # --- Route53 ---
  #
  # The server module always creates ACM cert + Route53 records (alias to ALB
  # and DNS-01 validation), so these statements are unconditional. Cloud Map's
  # CreatePrivateDnsNamespace also creates a Route53 private hosted zone
  # under the caller's identity, which needs the hosted-zone management
  # actions below.

  # Hosted-zone CRUD (private zones for Cloud Map, public-zone reads for the
  # caller-supplied zone). ChangeResourceRecordSets is the destructive action
  # and is split into a separate statement scoped to caller-supplied zones.
  statement {
    sid = "Route53HostedZoneManage"
    actions = [
      "route53:CreateHostedZone",
      "route53:GetHostedZone",
      "route53:DeleteHostedZone",
      "route53:ListHostedZones",
      "route53:ListHostedZonesByVPC",
      "route53:AssociateVPCWithHostedZone",
      "route53:DisassociateVPCFromHostedZone",
      "route53:ChangeTagsForResource",
      "route53:ListTagsForResource",
      "route53:ListResourceRecordSets",
    ]
    resources = ["*"]
  }

  # ChangeResourceRecordSets is scoped to caller-supplied public zones when
  # `route53_zone_ids` is set. Falls back to `*` only as a convenience for
  # callers who haven't enumerated their zones — strongly recommend setting
  # the variable.
  statement {
    sid     = "Route53RecordsForZones"
    actions = ["route53:ChangeResourceRecordSets"]
    resources = (
      length(var.route53_zone_ids) > 0
      ? [for zone_id in var.route53_zone_ids : "arn:${local.partition}:route53:::hostedzone/${zone_id}"]
      : ["*"]
    )
  }

  # GetChange takes a change-id, not a zone ARN — must be `*`.
  statement {
    sid       = "Route53GetChange"
    actions   = ["route53:GetChange"]
    resources = ["*"]
  }

  # --- ACM ---
  #
  # The server module always issues an ACM cert. ACM cert ARNs are only
  # known after RequestCertificate, so the destructive actions can't be
  # scoped further at policy-write time. Restrict the action set instead of
  # granting `acm:*`.
  statement {
    sid = "AcmManageCertificates"
    actions = [
      "acm:RequestCertificate",
      "acm:DescribeCertificate",
      "acm:DeleteCertificate",
      "acm:ListCertificates",
      "acm:ListTagsForCertificate",
      "acm:AddTagsToCertificate",
      "acm:RemoveTagsFromCertificate",
      "acm:UpdateCertificateOptions",
    ]
    resources = ["*"]
  }

  # --- KMS describe (AWS-managed keys for default EFS / Secrets encryption) ---
  statement {
    sid = "KmsDescribeAwsManaged"
    actions = [
      "kms:DescribeKey",
      "kms:ListAliases",
    ]
    resources = ["*"]
  }

  # --- Bedrock (model discovery) ---
  #
  # The task role grants `bedrock:InvokeModel` at runtime. The deployer
  # itself doesn't invoke; it only needs to validate the model exists when
  # rendering the task-role policy document. Read-only.
  statement {
    sid = "BedrockDescribe"
    actions = [
      "bedrock:GetFoundationModel",
      "bedrock:ListFoundationModels",
    ]
    resources = ["*"]
  }

  # --- STS ---
  statement {
    sid       = "StsCallerIdentity"
    actions   = ["sts:GetCallerIdentity"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "deployer" {
  name        = "${var.role_name}-policy"
  path        = var.role_path
  description = "Least-privilege policy for the nextcloud-mcp-server deployer role."
  policy      = data.aws_iam_policy_document.deployer.json
}

resource "aws_iam_role_policy_attachment" "deployer" {
  role       = aws_iam_role.this.name
  policy_arn = aws_iam_policy.deployer.arn
}
