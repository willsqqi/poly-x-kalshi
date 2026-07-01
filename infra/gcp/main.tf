terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.45"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_project" "current" {
  project_id = var.project_id
}

locals {
  labels = {
    app         = "poly-x-kalshi"
    component   = "fifa-scanner"
    environment = var.environment
  }

  scanner_image               = var.scanner_image != "" ? var.scanner_image : "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.scanner.repository_id}/fifa-scanner:latest"
  cloud_build_service_account = "${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}
