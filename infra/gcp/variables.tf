variable "project_id" {
  description = "GCP project ID that owns the scanner resources."
  type        = string
}

variable "region" {
  description = "GCP region for Cloud Run, Scheduler, and Artifact Registry."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Short environment name used in resource names."
  type        = string
  default     = "dev"
}

variable "name_prefix" {
  description = "Prefix for named resources."
  type        = string
  default     = "poly-x-kalshi"
}

variable "scanner_image" {
  description = "Fully qualified scanner image. If empty, Terraform points the job at the managed Artifact Registry latest tag."
  type        = string
  default     = ""
}

variable "schedule" {
  description = "Cloud Scheduler cron expression. Default is one snapshot per minute."
  type        = string
  default     = "* * * * *"
}

variable "sports_schedule" {
  description = "Cloud Scheduler cron expression for cross-sports snapshots."
  type        = string
  default     = "*/5 * * * *"
}

variable "sports_discovery_schedule" {
  description = "Cloud Scheduler cron expression for daily cross-sports discovery and review queue refresh."
  type        = string
  default     = "15 6 * * *"
}

variable "scheduler_paused" {
  description = "Keep the scheduler paused after creation. Set false to start collecting immediately."
  type        = bool
  default     = true
}

variable "sports_scheduler_paused" {
  description = "Keep the cross-sports scheduler paused after creation. Manual Cloud Run executions still work."
  type        = bool
  default     = true
}

variable "sports_discovery_scheduler_paused" {
  description = "Keep the daily cross-sports discovery scheduler paused after creation. Manual Cloud Run executions still work."
  type        = bool
  default     = true
}

variable "daily_pipeline_schedule" {
  description = "Cloud Scheduler cron expression for the Cloud SQL active-universe and matching pipeline."
  type        = string
  default     = "30 7 * * *"
}

variable "daily_pipeline_scheduler_paused" {
  description = "Keep the Cloud SQL daily pipeline scheduler paused after creation. Manual Cloud Run executions still work."
  type        = bool
  default     = true
}

variable "snapshot_timeout_seconds" {
  description = "Maximum runtime for each one-shot snapshot job."
  type        = number
  default     = 300
}

variable "sports_snapshot_timeout_seconds" {
  description = "Maximum runtime for each cross-sports snapshot job."
  type        = number
  default     = 600
}

variable "sports_discovery_timeout_seconds" {
  description = "Maximum runtime for each daily cross-sports discovery job."
  type        = number
  default     = 1800
}

variable "daily_pipeline_timeout_seconds" {
  description = "Maximum runtime for the daily Cloud SQL active-universe and matching pipeline."
  type        = number
  default     = 14400
}

variable "cpu" {
  description = "Cloud Run Job CPU limit."
  type        = string
  default     = "1"
}

variable "memory" {
  description = "Cloud Run Job memory limit."
  type        = string
  default     = "512Mi"
}

variable "sports_discovery_cpu" {
  description = "Cloud Run CPU limit for the heavier cross-sports discovery and semantic matching job."
  type        = string
  default     = "2"
}

variable "sports_discovery_memory" {
  description = "Cloud Run memory limit for the heavier cross-sports discovery and semantic matching job."
  type        = string
  default     = "2Gi"
}

variable "daily_pipeline_cpu" {
  description = "Cloud Run CPU limit for the Cloud SQL daily pipeline."
  type        = string
  default     = "2"
}

variable "daily_pipeline_memory" {
  description = "Cloud Run memory limit for the Cloud SQL daily pipeline."
  type        = string
  default     = "4Gi"
}

variable "daily_pipeline_kalshi_event_market_workers" {
  description = "Concurrent Kalshi event-market fetch workers for the daily Cloud SQL active-universe pipeline."
  type        = number
  default     = 2
}

variable "daily_pipeline_sports_only" {
  description = "Restrict the daily Cloud SQL active-universe pipeline to venue-tagged sports events and markets."
  type        = bool
  default     = true
}

variable "cloud_sql_database_version" {
  description = "Cloud SQL PostgreSQL database version."
  type        = string
  default     = "POSTGRES_15"
}

variable "cloud_sql_tier" {
  description = "Cloud SQL instance tier."
  type        = string
  default     = "db-f1-micro"
}

variable "cloud_sql_disk_size_gb" {
  description = "Cloud SQL disk size in GB."
  type        = number
  default     = 10
}

variable "cloud_sql_backup_enabled" {
  description = "Enable Cloud SQL automated backups."
  type        = bool
  default     = true
}

variable "cloud_sql_deletion_protection" {
  description = "Protect the Cloud SQL instance from accidental Terraform destroy."
  type        = bool
  default     = true
}

variable "cloud_sql_database_name" {
  description = "Application database name."
  type        = string
  default     = "prediction_market"
}

variable "cloud_sql_user" {
  description = "Application database user."
  type        = string
  default     = "prediction_market_app"
}

variable "market_limit" {
  description = "Discovery market limit. No-discovery mode ignores this for approved mapping price pulls."
  type        = number
  default     = 1000
}

variable "sports_market_limit" {
  description = "Discovery market limit for cross-sports snapshots. No-discovery mode ignores this for approved mapping price pulls."
  type        = number
  default     = 1500
}

variable "sports_discovery_semantic_embedding_provider" {
  description = "Semantic embedding provider for daily cross-sports discovery suggestions. Use vertex-gemini to enable Gemini embeddings."
  type        = string
  default     = "off"

  validation {
    condition     = contains(["off", "local", "vertex-gemini"], var.sports_discovery_semantic_embedding_provider)
    error_message = "sports_discovery_semantic_embedding_provider must be one of off, local, or vertex-gemini."
  }
}

variable "sports_discovery_semantic_embedding_dim" {
  description = "Output dimension for semantic embeddings in the daily discovery job."
  type        = number
  default     = 768
}

variable "sports_discovery_semantic_top_k" {
  description = "Maximum semantic suggestions to keep per Polymarket candidate in the daily discovery job."
  type        = number
  default     = 20
}

variable "sports_discovery_semantic_min_score" {
  description = "Minimum semantic combined score for review suggestions from the daily discovery job."
  type        = number
  default     = 72
}

variable "sports_discovery_semantic_batch_size" {
  description = "Vertex Gemini texts per embedding request for daily discovery."
  type        = number
  default     = 64
}

variable "sports_discovery_semantic_batch_sleep_seconds" {
  description = "Sleep between Vertex Gemini embedding batches to avoid token-per-minute quota failures."
  type        = number
  default     = 5
}

variable "sports_discovery_semantic_retry_initial_seconds" {
  description = "Initial sleep after Vertex Gemini quota errors."
  type        = number
  default     = 60
}

variable "sports_discovery_semantic_max_retries" {
  description = "Maximum retry attempts per Vertex Gemini embedding batch."
  type        = number
  default     = 8
}

variable "sports_discovery_semantic_cache_flush_batches" {
  description = "Flush market embedding cache every N semantic batches during daily discovery."
  type        = number
  default     = 2
}

variable "sports_discovery_semantic_max_embedding_texts" {
  description = "Maximum new uncached market texts to embed in a daily discovery run. Use 0 for unlimited."
  type        = number
  default     = 0
}

variable "sports_discovery_ai_pair_review_provider" {
  description = "Optional AI reviewer for generated discovery suggestions. Use vertex-gemini to annotate top suggestions for manual review."
  type        = string
  default     = "off"

  validation {
    condition     = contains(["off", "vertex-gemini"], var.sports_discovery_ai_pair_review_provider)
    error_message = "sports_discovery_ai_pair_review_provider must be one of off or vertex-gemini."
  }
}

variable "sports_discovery_ai_pair_review_model" {
  description = "Vertex Gemini model used by the optional AI pair reviewer."
  type        = string
  default     = "gemini-2.0-flash"
}

variable "sports_discovery_ai_pair_review_limit" {
  description = "Maximum generated suggestions to send to the AI reviewer in each discovery run. Use 0 for no cap."
  type        = number
  default     = 250
}

variable "sports_discovery_ai_pair_review_min_score" {
  description = "Minimum generated match score before a suggestion is sent to the AI reviewer."
  type        = number
  default     = 80
}

variable "orderbook_depth" {
  description = "Kalshi orderbook depth requested per mapped ticker."
  type        = number
  default     = 100
}

variable "min_net_edge" {
  description = "Minimum net edge required to mark a signal as an alert."
  type        = number
  default     = 0.02
}

variable "slippage_buffer_per_leg" {
  description = "Per-leg slippage buffer used in signal scoring."
  type        = number
  default     = 0.005
}

variable "fee_buffer_total" {
  description = "Total fee buffer used in signal scoring."
  type        = number
  default     = 0.01
}

variable "min_depth_per_leg" {
  description = "Minimum available depth required per leg."
  type        = number
  default     = 10
}

variable "raw_retention_days" {
  description = "Days to retain raw JSON snapshots in GCS."
  type        = number
  default     = 14
}
