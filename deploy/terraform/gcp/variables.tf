variable "project_id" {
  type        = string
  description = "GCP project ID"
}

variable "region" {
  type    = string
  default = "asia-southeast1"
}

variable "env" {
  type    = string
  default = "dev"
}

variable "db_tier" {
  type    = string
  default = "db-f1-micro"
}

variable "redis_memory_gb" {
  type    = number
  default = 1
}

variable "vm_machine_type" {
  type    = string
  default = "e2-standard-4"
}
