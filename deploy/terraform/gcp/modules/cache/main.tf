variable "name_prefix" { type = string }
variable "region" { type = string }
variable "memory_gb" { type = number }
variable "network_id" { type = string }

resource "google_redis_instance" "cache" {
  name               = "${var.name_prefix}-redis"
  tier               = "BASIC"
  memory_size_gb     = var.memory_gb
  region             = var.region
  redis_version      = "REDIS_7_0"
  authorized_network = var.network_id
  connect_mode       = "DIRECT_PEERING"
}

output "host" {
  value = google_redis_instance.cache.host
}

output "port" {
  value = google_redis_instance.cache.port
}
