resource "google_cloud_scheduler_job" "scanner" {
  name        = "${var.name_prefix}-${var.environment}-fifa-snapshot"
  description = "Runs one poly-x-kalshi FIFA arbitrage snapshot on a fixed schedule."
  region      = var.region
  schedule    = var.schedule
  paused      = var.scheduler_paused
  time_zone   = "Etc/UTC"

  retry_config {
    retry_count          = 1
    min_backoff_duration = "30s"
    max_backoff_duration = "120s"
    max_retry_duration   = "300s"
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.scanner.name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [
    google_project_service.required,
    google_project_iam_member.scheduler_run_developer,
    google_service_account_iam_member.scheduler_can_run_as_scanner,
  ]
}
