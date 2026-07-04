resource "random_password" "prediction_market_db" {
  length           = 32
  special          = true
  override_special = "_-#"
}

resource "google_sql_database_instance" "prediction_market" {
  name                = "${var.name_prefix}-${var.environment}-prediction-market"
  project             = var.project_id
  region              = var.region
  database_version    = var.cloud_sql_database_version
  deletion_protection = var.cloud_sql_deletion_protection

  settings {
    tier              = var.cloud_sql_tier
    activation_policy = var.cloud_sql_activation_policy
    availability_type = "ZONAL"
    disk_type         = "PD_SSD"
    disk_size         = var.cloud_sql_disk_size_gb
    disk_autoresize   = true

    backup_configuration {
      enabled    = var.cloud_sql_backup_enabled
      start_time = "07:00"
    }

    ip_configuration {
      ipv4_enabled = true
    }

    user_labels = local.labels
  }

  depends_on = [google_project_service.required]
}

resource "google_sql_database" "prediction_market" {
  name     = var.cloud_sql_database_name
  project  = var.project_id
  instance = google_sql_database_instance.prediction_market.name
}

resource "google_sql_user" "prediction_market_app" {
  name     = var.cloud_sql_user
  project  = var.project_id
  instance = google_sql_database_instance.prediction_market.name
  password = random_password.prediction_market_db.result
}

resource "google_secret_manager_secret" "prediction_market_db_password" {
  secret_id = "${var.name_prefix}-${var.environment}-prediction-market-db-password"
  labels    = local.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "prediction_market_db_password" {
  secret      = google_secret_manager_secret.prediction_market_db_password.id
  secret_data = random_password.prediction_market_db.result
}
