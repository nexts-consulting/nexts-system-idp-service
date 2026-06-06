variable "name_prefix" { type = string }
variable "location" { type = string }

resource "google_storage_bucket" "artifacts" {
  name                        = "${var.name_prefix}-artifacts"
  location                    = var.location
  uniform_bucket_level_access = true
  force_destroy               = true

  lifecycle_rule {
    condition { age = 90 }
    action { type = "Delete" }
  }
}

output "bucket_name" {
  value = google_storage_bucket.artifacts.name
}
