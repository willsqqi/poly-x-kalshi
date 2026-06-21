resource "aws_cloudwatch_log_group" "batch" {
  name              = local.batch_log_group_name
  retention_in_days = 30
}
