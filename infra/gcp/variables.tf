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

variable "scheduler_paused" {
  description = "Keep the scheduler paused after creation. Set false to start collecting immediately."
  type        = bool
  default     = true
}

variable "snapshot_timeout_seconds" {
  description = "Maximum runtime for each one-shot snapshot job."
  type        = number
  default     = 300
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

variable "market_limit" {
  description = "Discovery market limit. No-discovery mode ignores this for approved mapping price pulls."
  type        = number
  default     = 1000
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
