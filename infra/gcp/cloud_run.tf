resource "google_cloud_run_v2_job" "scanner" {
  name     = "${var.name_prefix}-${var.environment}-fifa-snapshot"
  location = var.region
  labels   = local.labels

  template {
    parallelism = 1
    task_count  = 1

    template {
      service_account = google_service_account.scanner.email
      timeout         = "${var.snapshot_timeout_seconds}s"
      max_retries     = 1

      containers {
        image = local.scanner_image
        args = [
          "--no-discovery",
          "--output-dir",
          "gs://${google_storage_bucket.scanner.name}/fifa_arbitrage",
          "--market-limit",
          tostring(var.market_limit),
          "--orderbook-depth",
          tostring(var.orderbook_depth),
          "--min-net-edge",
          tostring(var.min_net_edge),
          "--slippage-buffer-per-leg",
          tostring(var.slippage_buffer_per_leg),
          "--fee-buffer-total",
          tostring(var.fee_buffer_total),
          "--min-depth-per-leg",
          tostring(var.min_depth_per_leg),
        ]

        resources {
          limits = {
            cpu    = var.cpu
            memory = var.memory
          }
        }
      }
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_cloud_run_v2_job" "sports_scanner" {
  name     = "${var.name_prefix}-${var.environment}-sports-snapshot"
  location = var.region
  labels   = local.labels

  template {
    parallelism = 1
    task_count  = 1

    template {
      service_account = google_service_account.scanner.email
      timeout         = "${var.sports_snapshot_timeout_seconds}s"
      max_retries     = 1

      containers {
        image   = local.scanner_image
        command = ["poly-x-kalshi-sports-snapshot"]
        args = [
          "--no-discovery",
          "--output-dir",
          "gs://${google_storage_bucket.scanner.name}/cross_sports_arbitrage",
          "--mapping-path",
          "gs://${google_storage_bucket.scanner.name}/cross_sports_arbitrage/manual_review/approved_mappings/current.csv",
          "--market-limit",
          tostring(var.sports_market_limit),
          "--orderbook-depth",
          tostring(var.orderbook_depth),
          "--min-net-edge",
          tostring(var.min_net_edge),
          "--slippage-buffer-per-leg",
          tostring(var.slippage_buffer_per_leg),
          "--fee-buffer-total",
          tostring(var.fee_buffer_total),
          "--min-depth-per-leg",
          tostring(var.min_depth_per_leg),
        ]

        resources {
          limits = {
            cpu    = var.cpu
            memory = var.memory
          }
        }
      }
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_cloud_run_v2_job" "sports_discovery" {
  name     = "${var.name_prefix}-${var.environment}-sports-discovery"
  location = var.region
  labels   = local.labels

  template {
    parallelism = 1
    task_count  = 1

    template {
      service_account = google_service_account.scanner.email
      timeout         = "${var.sports_discovery_timeout_seconds}s"
      max_retries     = 1

      containers {
        image   = local.scanner_image
        command = ["poly-x-kalshi-sports-snapshot"]
        args = [
          "--discovery-only",
          "--output-dir",
          "gs://${google_storage_bucket.scanner.name}/cross_sports_arbitrage",
          "--mapping-path",
          "gs://${google_storage_bucket.scanner.name}/cross_sports_arbitrage/manual_review/approved_mappings/current.csv",
          "--market-limit",
          tostring(var.sports_market_limit),
          "--orderbook-depth",
          tostring(var.orderbook_depth),
          "--min-net-edge",
          tostring(var.min_net_edge),
          "--slippage-buffer-per-leg",
          tostring(var.slippage_buffer_per_leg),
          "--fee-buffer-total",
          tostring(var.fee_buffer_total),
          "--min-depth-per-leg",
          tostring(var.min_depth_per_leg),
          "--semantic-embedding-provider",
          var.sports_discovery_semantic_embedding_provider,
          "--semantic-embedding-dim",
          tostring(var.sports_discovery_semantic_embedding_dim),
          "--semantic-top-k",
          tostring(var.sports_discovery_semantic_top_k),
          "--semantic-min-score",
          tostring(var.sports_discovery_semantic_min_score),
          "--semantic-batch-size",
          tostring(var.sports_discovery_semantic_batch_size),
          "--semantic-batch-sleep-seconds",
          tostring(var.sports_discovery_semantic_batch_sleep_seconds),
          "--semantic-retry-initial-seconds",
          tostring(var.sports_discovery_semantic_retry_initial_seconds),
          "--semantic-max-retries",
          tostring(var.sports_discovery_semantic_max_retries),
          "--semantic-cache-flush-batches",
          tostring(var.sports_discovery_semantic_cache_flush_batches),
          "--semantic-max-embedding-texts",
          tostring(var.sports_discovery_semantic_max_embedding_texts),
          "--ai-pair-review-provider",
          var.sports_discovery_ai_pair_review_provider,
          "--ai-pair-review-model",
          var.sports_discovery_ai_pair_review_model,
          "--ai-pair-review-limit",
          tostring(var.sports_discovery_ai_pair_review_limit),
          "--ai-pair-review-min-score",
          tostring(var.sports_discovery_ai_pair_review_min_score),
        ]

        resources {
          limits = {
            cpu    = var.sports_discovery_cpu
            memory = var.sports_discovery_memory
          }
        }
      }
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_cloud_run_v2_job" "daily_pipeline" {
  name     = "${var.name_prefix}-${var.environment}-daily-pipeline"
  location = var.region
  labels   = local.labels

  template {
    parallelism = 1
    task_count  = 1

    template {
      service_account = google_service_account.scanner.email
      timeout         = "${var.daily_pipeline_timeout_seconds}s"
      max_retries     = 0

      containers {
        image   = local.scanner_image
        command = ["poly-x-kalshi-cloud-daily-pipeline"]
        args = concat(
          [
            "--gcs-output",
            "gs://${google_storage_bucket.scanner.name}/cross_sports_arbitrage",
            "--approved-market-pairs-path",
            "gs://${google_storage_bucket.scanner.name}/cross_sports_arbitrage/manual_review/approved_market_pairs/current.csv",
            "--semantic-embedding-provider",
            "vertex-gemini",
            "--semantic-embedding-dim",
            tostring(var.sports_discovery_semantic_embedding_dim),
            "--semantic-batch-size",
            tostring(var.sports_discovery_semantic_batch_size),
            "--semantic-batch-sleep-seconds",
            tostring(var.sports_discovery_semantic_batch_sleep_seconds),
            "--kalshi-event-market-workers",
            tostring(var.daily_pipeline_kalshi_event_market_workers),
          ],
          var.daily_pipeline_sports_only ? ["--sports-only"] : [],
        )

        env {
          name  = "POLY_X_KALSHI_DB_HOST"
          value = "/cloudsql/${google_sql_database_instance.prediction_market.connection_name}"
        }

        env {
          name  = "POLY_X_KALSHI_DB_NAME"
          value = google_sql_database.prediction_market.name
        }

        env {
          name  = "POLY_X_KALSHI_DB_USER"
          value = google_sql_user.prediction_market_app.name
        }

        env {
          name = "POLY_X_KALSHI_DB_PASSWORD"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.prediction_market_db_password.secret_id
              version = "latest"
            }
          }
        }

        resources {
          limits = {
            cpu    = var.daily_pipeline_cpu
            memory = var.daily_pipeline_memory
          }
        }

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.prediction_market.connection_name]
        }
      }
    }
  }

  depends_on = [
    google_project_service.required,
    google_sql_database.prediction_market,
    google_sql_user.prediction_market_app,
    google_secret_manager_secret_iam_member.scanner_db_password_accessor,
    google_project_iam_member.scanner_cloud_sql_client,
  ]
}
