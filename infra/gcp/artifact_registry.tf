resource "google_artifact_registry_repository" "scanner" {
  location      = var.region
  repository_id = "${var.name_prefix}-${var.environment}"
  description   = "Docker images for the poly-x-kalshi FIFA scanner"
  format        = "DOCKER"
  labels        = local.labels

  depends_on = [google_project_service.required]
}
