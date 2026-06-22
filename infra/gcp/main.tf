terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.45"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  labels = {
    app         = "poly-x-kalshi"
    component   = "fifa-scanner"
    environment = var.environment
  }

  scanner_image = var.scanner_image != "" ? var.scanner_image : "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.scanner.repository_id}/fifa-scanner:latest"
}
