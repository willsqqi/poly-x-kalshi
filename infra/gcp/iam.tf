resource "google_service_account" "scanner" {
  account_id   = "${var.name_prefix}-${var.environment}-scanner"
  display_name = "poly-x-kalshi FIFA scanner"

  depends_on = [google_project_service.required]
}

resource "google_service_account" "scheduler" {
  account_id   = "${var.name_prefix}-${var.environment}-schedule"
  display_name = "poly-x-kalshi FIFA scanner scheduler"

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket_iam_member" "scanner_object_admin" {
  bucket = google_storage_bucket.scanner.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.scanner.email}"
}

resource "google_project_iam_member" "scanner_vertex_ai_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.scanner.email}"
}

resource "google_project_iam_member" "scanner_cloud_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.scanner.email}"
}

resource "google_secret_manager_secret_iam_member" "scanner_db_password_accessor" {
  secret_id = google_secret_manager_secret.prediction_market_db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.scanner.email}"
}

resource "google_project_iam_member" "scheduler_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_service_account_iam_member" "scheduler_can_run_as_scanner" {
  service_account_id = google_service_account.scanner.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_project_iam_member" "cloud_build_storage_object_viewer" {
  project = var.project_id
  role    = "roles/storage.objectViewer"
  member  = "serviceAccount:${local.cloud_build_service_account}"
}

resource "google_project_iam_member" "cloud_build_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${local.cloud_build_service_account}"
}

resource "google_project_iam_member" "cloud_build_artifact_registry_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${local.cloud_build_service_account}"
}
