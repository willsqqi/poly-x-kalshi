output "artifact_registry_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.scanner.repository_id}"
}

output "default_scanner_image" {
  value = local.scanner_image
}

output "gcs_output_uri" {
  value = "gs://${google_storage_bucket.scanner.name}/fifa_arbitrage"
}

output "sports_gcs_output_uri" {
  value = "gs://${google_storage_bucket.scanner.name}/cross_sports_arbitrage"
}

output "cloud_run_job_name" {
  value = google_cloud_run_v2_job.scanner.name
}

output "sports_cloud_run_job_name" {
  value = google_cloud_run_v2_job.sports_scanner.name
}

output "sports_discovery_cloud_run_job_name" {
  value = google_cloud_run_v2_job.sports_discovery.name
}

output "daily_pipeline_cloud_run_job_name" {
  value = google_cloud_run_v2_job.daily_pipeline.name
}

output "cloud_scheduler_job_name" {
  value = google_cloud_scheduler_job.scanner.name
}

output "sports_cloud_scheduler_job_name" {
  value = google_cloud_scheduler_job.sports_scanner.name
}

output "sports_discovery_cloud_scheduler_job_name" {
  value = google_cloud_scheduler_job.sports_discovery.name
}

output "daily_pipeline_cloud_scheduler_job_name" {
  value = google_cloud_scheduler_job.daily_pipeline.name
}

output "scheduler_paused" {
  value = google_cloud_scheduler_job.scanner.paused
}

output "sports_scheduler_paused" {
  value = google_cloud_scheduler_job.sports_scanner.paused
}

output "sports_discovery_scheduler_paused" {
  value = google_cloud_scheduler_job.sports_discovery.paused
}

output "daily_pipeline_scheduler_paused" {
  value = google_cloud_scheduler_job.daily_pipeline.paused
}

output "manual_run_command" {
  value = "gcloud run jobs execute ${google_cloud_run_v2_job.scanner.name} --region ${var.region} --wait"
}

output "sports_manual_run_command" {
  value = "gcloud run jobs execute ${google_cloud_run_v2_job.sports_scanner.name} --region ${var.region} --wait"
}

output "sports_discovery_manual_run_command" {
  value = "gcloud run jobs execute ${google_cloud_run_v2_job.sports_discovery.name} --region ${var.region} --wait"
}

output "daily_pipeline_manual_run_command" {
  value = "gcloud run jobs execute ${google_cloud_run_v2_job.daily_pipeline.name} --region ${var.region} --wait"
}

output "sports_review_mapping_uri" {
  value = "gs://${google_storage_bucket.scanner.name}/cross_sports_arbitrage/manual_review/approved_mappings/current.csv"
}

output "sports_approved_market_pairs_uri" {
  value = "gs://${google_storage_bucket.scanner.name}/cross_sports_arbitrage/manual_review/approved_market_pairs/current.csv"
}

output "cloud_sql_connection_name" {
  value = google_sql_database_instance.prediction_market.connection_name
}

output "cloud_sql_database_name" {
  value = google_sql_database.prediction_market.name
}
