variable "name_prefix" { type = string }
variable "project_id" { type = string }
variable "artifacts_bucket" { type = string }

resource "google_service_account" "app" {
  account_id   = "${var.name_prefix}-app"
  display_name = "IDP App Service"
}

resource "google_service_account" "preprocess" {
  account_id   = "${var.name_prefix}-preprocess"
  display_name = "IDP Preprocess Service"
}

resource "google_service_account" "orchestrator" {
  account_id   = "${var.name_prefix}-orch"
  display_name = "IDP Orchestrator Service"
}

resource "google_storage_bucket_iam_member" "preprocess_writer" {
  bucket = var.artifacts_bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.preprocess.email}"
}

resource "google_storage_bucket_iam_member" "orchestrator_reader" {
  bucket = var.artifacts_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.orchestrator.email}"
}

output "app_sa_email" {
  value = google_service_account.app.email
}

output "preprocess_sa_email" {
  value = google_service_account.preprocess.email
}

output "orchestrator_sa_email" {
  value = google_service_account.orchestrator.email
}
