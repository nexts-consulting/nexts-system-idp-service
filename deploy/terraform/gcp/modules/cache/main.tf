variable "name_prefix" { type = string }
variable "region" { type = string }
variable "memory_gb" { type = number }
variable "network_id" { type = string }

resource "google_redis_instance" "cache" {
  name           = "${var.name_prefix}-redis"
  tier           = "BASIC"
  memory_size_gb = var.memory_gb
  region         = var.region
  redis_version  = "REDIS_7_0"
}

output "host" {
  value = google_redis_instance.cache.host
}
