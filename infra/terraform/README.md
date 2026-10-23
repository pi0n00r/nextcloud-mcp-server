# Terraform modules for `nextcloud-mcp-server` on AWS

POC Terraform modules to spin up a `nextcloud-mcp-server` on AWS ECS Fargate,
behind an ALB, with EFS-backed task storage. Two Qdrant modes are supported:
an in-VPC Qdrant ECS task (default) or an external/managed Qdrant service
(URL + API key supplied via Secrets Manager).

This directory ships **two** modules:

| Module | Purpose |
|---|---|
| [`nextcloud-mcp-deployer-role`](./nextcloud-mcp-deployer-role) | A least-privilege IAM role + policy scoped to deploying the MCP server module. Apply this **first**, in the target AWS account, with a privileged bootstrap principal. |
| [`nextcloud-mcp-server`](./nextcloud-mcp-server) | The actual MCP server: ECS cluster, ALB, EFS, IAM task/execution roles, Route53 record, ACM certificate, optional Qdrant ECS service. Apply this **second**, by assuming the deployer role. |

## Two-phase deploy

```
┌──────────────┐     ┌────────────────────────────┐     ┌─────────────────────┐
│  Bootstrap   │────▶│  Apply deployer-role       │────▶│  Apply mcp-server   │
│  principal   │     │  (creates an IAM role +    │     │  (assume the role,  │
│  (admin)     │     │  scoped policy)            │     │  provision the     │
│              │     │                            │     │  MCP server)        │
└──────────────┘     └────────────────────────────┘     └─────────────────────┘
```

The split lets a customer create a stable, minimum-permission role in their
account once, and then re-apply the MCP server module repeatedly under that
role without ever exposing admin credentials to the deploy pipeline.

## Phase 1 — bootstrap the deployer role

### Prerequisite: bootstrap IAM policy

The principal that applies `nextcloud-mcp-deployer-role` only needs IAM
permissions to manage the role and its attached policy. The policy below is
**scoped to the module defaults** (`role_path = /clients/`,
`role_name = nextcloud-mcp-deployer`); widen the resource ARNs if you
override either input.

Copy this into the AWS console (IAM → Policies → Create policy → JSON) and
attach it to the user / role that runs `terraform apply` for the deployer-role
module:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BootstrapManageDeployerRole",
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole",
        "iam:GetRole",
        "iam:DeleteRole",
        "iam:UpdateRole",
        "iam:UpdateAssumeRolePolicy",
        "iam:TagRole",
        "iam:UntagRole",
        "iam:ListRoleTags",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:ListAttachedRolePolicies",
        "iam:ListRolePolicies",
        "iam:PutRolePolicy",
        "iam:GetRolePolicy",
        "iam:DeleteRolePolicy"
      ],
      "Resource": "arn:aws:iam::*:role/clients/nextcloud-mcp-deployer*"
    },
    {
      "Sid": "BootstrapManageDeployerPolicy",
      "Effect": "Allow",
      "Action": [
        "iam:CreatePolicy",
        "iam:GetPolicy",
        "iam:GetPolicyVersion",
        "iam:ListPolicyVersions",
        "iam:CreatePolicyVersion",
        "iam:DeletePolicyVersion",
        "iam:DeletePolicy",
        "iam:TagPolicy",
        "iam:UntagPolicy",
        "iam:ListEntitiesForPolicy"
      ],
      "Resource": "arn:aws:iam::*:policy/clients/nextcloud-mcp-deployer-policy"
    },
    {
      "Sid": "BootstrapStsCallerIdentity",
      "Effect": "Allow",
      "Action": ["sts:GetCallerIdentity"],
      "Resource": "*"
    }
  ]
}
```

### Apply the deployer-role module

```hcl
terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

provider "aws" {
  region = "eu-west-1"
}

module "nextcloud_mcp_deployer_role" {
  source = "git::https://github.com/cbcoutinho/nextcloud-mcp-server.git//infra/terraform/nextcloud-mcp-deployer-role?ref=master"

  trusted_principal_arns = [
    "arn:aws:iam::123456789012:root", # principal that will assume the role
  ]

  # Optional: set true if the Secrets Manager secret is managed in the same TF run as the MCP server.
  # allow_secret_create = true

  # Optional: required only if you use the module's custom-domain (Route53 + ACM) mode.
  # route53_zone_ids = ["Z0123456789ABCDEFGHIJ"]
}

output "deployer_role_arn" {
  value = module.nextcloud_mcp_deployer_role.role_arn
}
```

`terraform apply` produces a role ARN like
`arn:aws:iam::<account-id>:role/clients/nextcloud-mcp-deployer`. Hand that
ARN to whichever pipeline / human applies Phase 2.

## Phase 2 — deploy the MCP server

### Provider configured to assume the deployer role

```hcl
provider "aws" {
  region = "eu-west-1"
  assume_role {
    role_arn = "arn:aws:iam::123456789012:role/clients/nextcloud-mcp-deployer"
    # external_id = "..." # if you set one in the deployer-role trust policy
  }
}
```

### Pre-existing requirements

- A VPC with **public subnets** (for the ALB and ECS task ENIs — the module
  runs tasks with `assign_public_ip = true`, no NAT gateway needed) and
  **private subnets** in matching AZs (for EFS mount targets).
- A Route53 **public hosted zone** the module can write to. The module
  generates a random `<two-words>.<your-zone>` subdomain and provisions the
  ACM cert + DNS records itself.
- A Secrets Manager secret with a **JSON value** containing the keys below.
  Create it manually, or via Terraform (set `allow_secret_create = true` on
  the deployer role and manage it in the same plan).

  Required keys:
  ```json
  {
    "host": "https://your-nextcloud.example.com",
    "client_id": "<oidc-client-id>",
    "client_secret": "<oidc-client-secret>",
    "token_encryption_key": "<random 32-byte url-safe base64>",
    "webhook_secret": "<random shared secret>"
  }
  ```

  When `use_external_qdrant = true`, also include:
  ```json
  {
    "qdrant_url": "https://<your-qdrant-cluster>.cloud.qdrant.io:6333",
    "qdrant_api_key": "<qdrant api key>"
  }
  ```

### Mode A — in-VPC Qdrant (default)

Runs Qdrant as a second Fargate task on the same ECS cluster, exposed to the
MCP server via Cloud Map private DNS. Storage on EFS.

```hcl
module "nextcloud_mcp_server" {
  source = "git::https://github.com/cbcoutinho/nextcloud-mcp-server.git//infra/terraform/nextcloud-mcp-server?ref=master"

  vpc_id             = "vpc-0123456789abcdef0"
  public_subnet_ids  = ["subnet-aaa", "subnet-bbb"]
  private_subnet_ids = ["subnet-ccc", "subnet-ddd"]

  zone_id   = "Z0123456789ABCDEFGHIJ"
  zone_name = "example.com"

  nextcloud_url = "https://nextcloud.example.com"

  image_tag        = "0.75.2"   # pin to a release; never :latest
  qdrant_image_tag = "v1.17.1"

  secret_arn = "arn:aws:secretsmanager:eu-west-1:123456789012:secret:nextcloud-mcp-aws-env-XXXXXX"
}

output "mcp_url" {
  value = module.nextcloud_mcp_server.url
}
```

### Mode B — external / managed Qdrant

Skip the Qdrant ECS task entirely; point the MCP server at a managed Qdrant
cluster (e.g. Qdrant Cloud, or a Qdrant you run elsewhere). `qdrant_url` and
`qdrant_api_key` come from the same Secrets Manager secret.

```hcl
module "nextcloud_mcp_server" {
  source = "git::https://github.com/cbcoutinho/nextcloud-mcp-server.git//infra/terraform/nextcloud-mcp-server?ref=master"

  vpc_id             = "vpc-0123456789abcdef0"
  public_subnet_ids  = ["subnet-aaa", "subnet-bbb"]
  private_subnet_ids = ["subnet-ccc", "subnet-ddd"]

  zone_id   = "Z0123456789ABCDEFGHIJ"
  zone_name = "example.com"

  nextcloud_url = "https://nextcloud.example.com"

  image_tag = "0.75.2"

  secret_arn = "arn:aws:secretsmanager:eu-west-1:123456789012:secret:nextcloud-mcp-aws-env-XXXXXX"

  use_external_qdrant = true
  qdrant_collection   = "nextcloud-mcp"
  # qdrant_image_tag intentionally omitted — only required when use_external_qdrant = false.
}
```

## Pinning the module version

`?ref=master` is fine for a POC but pins to a moving target. Once a release
tag exists in this repo (e.g. `infra-tf-v0.1.0`), pin to it:

```hcl
source = "git::https://github.com/cbcoutinho/nextcloud-mcp-server.git//infra/terraform/nextcloud-mcp-server?ref=infra-tf-v0.1.0"
```

## Module-specific docs

Each module's full input/output reference lives in its own `README.md`:

- [`nextcloud-mcp-server/README.md`](./nextcloud-mcp-server/README.md)
- [`nextcloud-mcp-deployer-role/README.md`](./nextcloud-mcp-deployer-role/README.md)
