resource "google_storage_bucket" "scanner" {
  name                        = "${var.name_prefix}-${var.environment}-${var.project_id}-scanner"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false
  labels                      = local.labels

  lifecycle_rule {
    condition {
      age            = var.raw_retention_days
      matches_prefix = ["fifa_arbitrage/raw/"]
      with_state     = "ANY"
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.required]
}
