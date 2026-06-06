output "artifacts_bucket" {
  value = module.storage.bucket_name
}

output "cloud_sql_connection" {
  value     = module.database.connection_name
  sensitive = true
}

output "redis_host" {
  value = module.cache.host
}

output "app_vm_ip" {
  value = module.compute.app_vm_ip
}

output "monitoring_vm_ip" {
  value = module.observability.monitoring_vm_ip
}

output "app_service_account" {
  value = module.iam.app_sa_email
}
