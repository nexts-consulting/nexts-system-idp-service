variable "name_prefix" { type = string }
variable "region" { type = string }
variable "tier" { type = string }
variable "network_id" { type = string }

resource "google_sql_database_instance" "main" {
  name             = "${var.name_prefix}-pg"
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier = var.tier
    ip_configuration {
      ipv4_enabled = true
    }
  }

  deletion_protection = false
}

resource "google_sql_database" "idp" {
  name     = "idp"
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "idp" {
  name     = "idp"
  instance = google_sql_database_instance.main.name
  password = "nexts-system-idp-service@2026"
}

output "connection_name" {
  value = google_sql_database_instance.main.connection_name
}
