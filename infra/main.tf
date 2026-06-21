terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

locals {
  name_prefix           = "${var.project_name}-${var.environment}"
  data_bucket_name      = coalesce(var.data_bucket_name, "${local.name_prefix}-${data.aws_caller_identity.current.account_id}-${var.aws_region}-data")
  athena_bucket_name    = coalesce(var.athena_results_bucket_name, "${local.name_prefix}-${data.aws_caller_identity.current.account_id}-${var.aws_region}-athena")
  data_lake_uri         = "s3://${aws_s3_bucket.data.bucket}"
  glue_database_name    = replace("${var.project_name}_${var.environment}", "-", "_")
  batch_log_group_name  = "/aws/batch/${local.name_prefix}"
  etl_image             = "${aws_ecr_repository.etl.repository_url}:${var.image_tag}"
  polygon_gold_location = "s3://${aws_s3_bucket.data.bucket}/gold/polymarket"
}
