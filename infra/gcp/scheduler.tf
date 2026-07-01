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

resource "google_cloud_scheduler_job" "sports_scanner" {
  name        = "${var.name_prefix}-${var.environment}-sports-snapshot"
  description = "Runs one poly-x-kalshi cross-sports arbitrage snapshot on a fixed schedule."
  region      = var.region
  schedule    = var.sports_schedule
  paused      = var.sports_scheduler_paused
  time_zone   = "Etc/UTC"

  retry_config {
    retry_count          = 1
    min_backoff_duration = "30s"
    max_backoff_duration = "120s"
    max_retry_duration   = "300s"
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.sports_scanner.name}:run"

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

resource "google_cloud_scheduler_job" "sports_discovery" {
  name        = "${var.name_prefix}-${var.environment}-sports-discovery"
  description = "Runs daily cross-sports discovery so the manual review queue sees fresh active events."
  region      = var.region
  schedule    = var.sports_discovery_schedule
  paused      = var.sports_discovery_scheduler_paused
  time_zone   = "Etc/UTC"

  retry_config {
    retry_count          = 1
    min_backoff_duration = "30s"
    max_backoff_duration = "120s"
    max_retry_duration   = "300s"
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.sports_discovery.name}:run"

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

resource "google_cloud_scheduler_job" "daily_pipeline" {
  name        = "${var.name_prefix}-${var.environment}-daily-pipeline"
  description = "Runs the daily Cloud SQL active-universe sync and Vertex AI matching candidate pipeline."
  region      = var.region
  schedule    = var.daily_pipeline_schedule
  paused      = var.daily_pipeline_scheduler_paused
  time_zone   = "Etc/UTC"

  retry_config {
    retry_count          = 0
    min_backoff_duration = "60s"
    max_backoff_duration = "300s"
    max_retry_duration   = "600s"
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.daily_pipeline.name}:run"

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
