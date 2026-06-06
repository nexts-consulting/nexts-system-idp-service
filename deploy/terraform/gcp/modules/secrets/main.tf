variable "name_prefix" { type = string }

resource "google_secret_manager_secret" "db_url" {
  secret_id = "${var.name_prefix}-database-url"
  
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "runpod_api_key" {
  secret_id = "${var.name_prefix}-runpod-api-key"
  
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "callback_hmac" {
  secret_id = "${var.name_prefix}-callback-hmac-secret"
  
  replication {
    auto {}
  }
}