variable "name_prefix" { type = string }
variable "region" { type = string }
variable "zone" { type = string }
variable "machine_type" { type = string }
variable "network_id" { type = string }
variable "subnet_id" { type = string }
variable "app_sa_email" { type = string }
variable "preprocess_sa_email" { type = string }
variable "orchestrator_sa_email" { type = string }

resource "google_compute_instance" "app_vm" {
  name         = "${var.name_prefix}-app-vm"
  machine_type = var.machine_type
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 50
    }
  }

  network_interface {
    subnetwork = var.subnet_id
    access_config {}
  }

  service_account {
    email  = var.app_sa_email
    scopes = ["cloud-platform"]
  }

  metadata_startup_script = <<-EOF
    #!/bin/bash
    apt-get update && apt-get install -y docker.io docker-compose-plugin
    # Clone repo and run: docker compose -f deploy/docker/docker-compose.yml up -d
  EOF

  tags = ["idp-app"]
}

resource "google_compute_firewall" "allow_http" {
  name    = "${var.name_prefix}-allow-http"
  network = var.network_id

  allow {
    protocol = "tcp"
    ports    = ["8000", "8001", "8002", "3000", "9090"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["idp-app"]
}

output "app_vm_ip" {
  value = google_compute_instance.app_vm.network_interface[0].access_config[0].nat_ip
}
