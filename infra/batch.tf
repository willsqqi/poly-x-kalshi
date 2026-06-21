resource "aws_batch_compute_environment" "fargate" {
  compute_environment_name = "${local.name_prefix}-fargate"
  type                     = "MANAGED"
  service_role             = aws_iam_role.batch_service.arn

  compute_resources {
    max_vcpus          = var.batch_max_vcpus
    security_group_ids = var.batch_security_group_ids
    subnets            = var.batch_subnet_ids
    type               = "FARGATE"
  }

  depends_on = [aws_iam_role_policy_attachment.batch_service]
}

resource "aws_batch_job_queue" "etl" {
  name     = "${local.name_prefix}-etl"
  state    = "ENABLED"
  priority = 1

  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.fargate.arn
  }
}

resource "aws_batch_job_definition" "etl" {
  name                  = "${local.name_prefix}-etl"
  type                  = "container"
  platform_capabilities = ["FARGATE"]

  container_properties = jsonencode({
    image            = local.etl_image
    executionRoleArn = aws_iam_role.batch_execution.arn
    jobRoleArn       = aws_iam_role.batch_job.arn
    command          = ["--job", "fetch_orderfilled"]
    resourceRequirements = [
      { type = "VCPU", value = var.batch_job_vcpu },
      { type = "MEMORY", value = var.batch_job_memory }
    ]
    environment = [
      { name = "PREDICTION_MARKET_LAKE_URI", value = local.data_lake_uri },
      { name = "POLYGON_RPC_SECRET_ID", value = aws_secretsmanager_secret.polygon_rpc.arn }
    ]
    secrets = [
      { name = "POLYGON_RPC_URL", valueFrom = aws_secretsmanager_secret.polygon_rpc.arn }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.batch.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "etl"
      }
    }
    networkConfiguration = {
      assignPublicIp = "ENABLED"
    }
  })
}
