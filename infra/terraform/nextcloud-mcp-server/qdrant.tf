locals {
  qdrant_name = "${var.name}-qdrant"
  qdrant_port = 6333
}

resource "aws_cloudwatch_log_group" "qdrant" {
  count             = var.use_external_qdrant ? 0 : 1
  name              = "/ecs/${local.qdrant_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_security_group" "qdrant" {
  count       = var.use_external_qdrant ? 0 : 1
  name        = local.qdrant_name
  description = "Qdrant ECS task ENI for ${var.name}"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "qdrant_from_task" {
  count                        = var.use_external_qdrant ? 0 : 1
  security_group_id            = aws_security_group.qdrant[0].id
  referenced_security_group_id = aws_security_group.task.id
  from_port                    = local.qdrant_port
  to_port                      = local.qdrant_port
  ip_protocol                  = "tcp"
  description                  = "Qdrant REST from mcp-server"
}

resource "aws_vpc_security_group_egress_rule" "qdrant_all_v4" {
  count             = var.use_external_qdrant ? 0 : 1
  security_group_id = aws_security_group.qdrant[0].id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_egress_rule" "qdrant_all_v6" {
  count             = var.use_external_qdrant ? 0 : 1
  security_group_id = aws_security_group.qdrant[0].id
  cidr_ipv6         = "::/0"
  ip_protocol       = "-1"
}

resource "aws_service_discovery_service" "qdrant" {
  count = var.use_external_qdrant ? 0 : 1
  name  = "qdrant"

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.this.id
    routing_policy = "MULTIVALUE"

    dns_records {
      type = "A"
      ttl  = 10
    }
  }
}

resource "aws_ecs_task_definition" "qdrant" {
  count                    = var.use_external_qdrant ? 0 : 1
  family                   = local.qdrant_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.qdrant_cpu)
  memory                   = tostring(var.qdrant_memory)
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.qdrant_task[0].arn

  volume {
    name = "storage"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.this.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.qdrant[0].id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "qdrant"
      image     = "${var.qdrant_image}:${var.qdrant_image_tag}"
      essential = true

      portMappings = [
        {
          containerPort = local.qdrant_port
          protocol      = "tcp"
        },
      ]

      mountPoints = [
        { sourceVolume = "storage", containerPath = "/qdrant/storage", readOnly = false },
      ]

      # No container-level healthCheck: qdrant's image (debian:13-slim) ships
      # only ca-certificates/tzdata/libunwind8 — no bash, no curl/wget — and
      # the qdrant binary has no health subcommand. ECS still detects process
      # exit; Cloud Map only routes to RUNNING tasks.

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.qdrant[0].name
          awslogs-region        = data.aws_region.current.region
          awslogs-stream-prefix = "ecs"
        }
      }
    },
  ])
}

# Qdrant uses local file storage with a portalocker file lock; concurrent
# instances can't share the EFS path. Stop the old task before starting the
# new one (brief downtime per qdrant deploy — rare). AZ rebalancing requires
# maxPercent>100, which would re-introduce the overlap, so it's disabled.
# The mcp-server service no longer pays this cost.
resource "aws_ecs_service" "qdrant" {
  count           = var.use_external_qdrant ? 0 : 1
  name            = local.qdrant_name
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.qdrant[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  availability_zone_rebalancing      = "DISABLED"

  enable_execute_command = true

  network_configuration {
    subnets          = var.public_subnet_ids
    security_groups  = [aws_security_group.qdrant[0].id]
    assign_public_ip = true
  }

  service_registries {
    registry_arn = aws_service_discovery_service.qdrant[0].arn
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [
    aws_efs_mount_target.this,
  ]
}
