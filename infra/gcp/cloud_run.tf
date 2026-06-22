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
