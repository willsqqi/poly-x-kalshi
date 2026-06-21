output "data_lake_uri" {
  value = local.data_lake_uri
}

output "ecr_repository_url" {
  value = aws_ecr_repository.etl.repository_url
}

output "batch_job_queue_arn" {
  value = aws_batch_job_queue.etl.arn
}

output "batch_job_definition_arn" {
  value = aws_batch_job_definition.etl.arn
}

output "glue_database_name" {
  value = aws_glue_catalog_database.phase1.name
}

output "athena_workgroup_name" {
  value = aws_athena_workgroup.phase1.name
}

output "polygon_rpc_secret_arn" {
  value = aws_secretsmanager_secret.polygon_rpc.arn
}
