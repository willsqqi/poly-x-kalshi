output "artifact_registry_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.scanner.repository_id}"
}

output "default_scanner_image" {
  value = local.scanner_image
}

output "gcs_output_uri" {
  value = "gs://${google_storage_bucket.scanner.name}/fifa_arbitrage"
}

output "cloud_run_job_name" {
  value = google_cloud_run_v2_job.scanner.name
}

output "cloud_scheduler_job_name" {
  value = google_cloud_scheduler_job.scanner.name
}

output "scheduler_paused" {
  value = google_cloud_scheduler_job.scanner.paused
}

output "manual_run_command" {
  value = "gcloud run jobs execute ${google_cloud_run_v2_job.scanner.name} --region ${var.region} --wait"
}
