variable "name_prefix" { type = string }
variable "region" { type = string }
variable "zone" { type = string }
variable "network_id" { type = string }
variable "subnet_id" { type = string }
variable "project_id" { type = string }

resource "google_compute_instance" "monitoring" {
  name         = "${var.name_prefix}-monitoring"
  machine_type = "e2-standard-2"
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 30
    }
  }

  network_interface {
    subnetwork = var.subnet_id
    access_config {}
  }

  metadata_startup_script = <<-EOF
    #!/bin/bash
    apt-get update && apt-get install -y docker.io
    # Deploy infra/monitoring via docker compose (prometheus, grafana, alertmanager, otel-collector)
  EOF

  tags = ["idp-monitoring"]
}

resource "google_monitoring_uptime_check_config" "app_health" {
  display_name = "${var.name_prefix}-app-health"
  timeout      = "10s"
  period       = "60s"

  http_check {
    path         = "/health"
    port         = "8000"
    use_ssl      = false
    validate_ssl = false
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = google_compute_instance.monitoring.network_interface[0].access_config[0].nat_ip
    }
  }
}

resource "google_monitoring_alert_policy" "vm_disk" {
  display_name = "${var.name_prefix}-high-disk"
  combiner     = "OR"

  conditions {
    display_name = "Disk utilization"
    condition_threshold {
      filter          = "resource.type = \"gce_instance\" AND metric.type = \"agent.googleapis.com/disk/percent_used\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = 85
    }
  }

  notification_channels = []
  alert_strategy {
    auto_close = "1800s"
  }
}

output "monitoring_vm_ip" {
  value = google_compute_instance.monitoring.network_interface[0].access_config[0].nat_ip
}
