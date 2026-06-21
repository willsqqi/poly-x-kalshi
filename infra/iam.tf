data "aws_iam_policy_document" "batch_service_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["batch.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "batch_service" {
  name               = "${local.name_prefix}-batch-service"
  assume_role_policy = data.aws_iam_policy_document.batch_service_assume_role.json
}

resource "aws_iam_role_policy_attachment" "batch_service" {
  role       = aws_iam_role.batch_service.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole"
}

data "aws_iam_policy_document" "ecs_tasks_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "batch_execution" {
  name               = "${local.name_prefix}-batch-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume_role.json
}

resource "aws_iam_role_policy_attachment" "batch_execution" {
  role       = aws_iam_role.batch_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "batch_execution_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.polygon_rpc.arn]
  }
}

resource "aws_iam_role_policy" "batch_execution_secrets" {
  name   = "${local.name_prefix}-batch-execution-secrets"
  role   = aws_iam_role.batch_execution.id
  policy = data.aws_iam_policy_document.batch_execution_secrets.json
}

resource "aws_iam_role" "batch_job" {
  name               = "${local.name_prefix}-batch-job"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume_role.json
}

data "aws_iam_policy_document" "batch_job" {
  statement {
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket"
    ]
    resources = [
      aws_s3_bucket.data.arn,
      "${aws_s3_bucket.data.arn}/*",
      aws_s3_bucket.athena_results.arn,
      "${aws_s3_bucket.athena_results.arn}/*"
    ]
  }

  statement {
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:CreatePartition",
      "glue:BatchCreatePartition",
      "glue:UpdatePartition"
    ]
    resources = ["*"]
  }

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.polygon_rpc.arn]
  }
}

resource "aws_iam_role_policy" "batch_job" {
  name   = "${local.name_prefix}-batch-job"
  role   = aws_iam_role.batch_job.id
  policy = data.aws_iam_policy_document.batch_job.json
}
