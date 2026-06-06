locals {
  name_prefix = "idp-${var.env}"
}

module "apis" {
  source     = "./modules/apis"
  project_id = var.project_id
}

module "network" {
  source      = "./modules/network"
  name_prefix = local.name_prefix
  region      = var.region
}

module "storage" {
  source      = "./modules/storage"
  name_prefix = local.name_prefix
  location    = var.region
}

module "database" {
  source      = "./modules/database"
  name_prefix = local.name_prefix
  region      = var.region
  tier        = var.db_tier
  network_id  = module.network.network_id
}

module "cache" {
  source      = "./modules/cache"
  name_prefix = local.name_prefix
  region      = var.region
  memory_gb   = var.redis_memory_gb
  network_id  = module.network.network_id
}

module "iam" {
  source             = "./modules/iam"
  name_prefix        = local.name_prefix
  project_id         = var.project_id
  artifacts_bucket   = module.storage.bucket_name
}

module "compute" {
  source         = "./modules/compute"
  name_prefix    = local.name_prefix
  region         = var.region
  zone           = "${var.region}-a"
  machine_type   = var.vm_machine_type
  network_id     = module.network.network_id
  subnet_id      = module.network.subnet_id
  app_sa_email   = module.iam.app_sa_email
  preprocess_sa_email = module.iam.preprocess_sa_email
  orchestrator_sa_email = module.iam.orchestrator_sa_email
}

module "secrets" {
  source      = "./modules/secrets"
  name_prefix = local.name_prefix

  depends_on = [module.apis]
}

module "observability" {
  source         = "./modules/observability"
  name_prefix    = local.name_prefix
  region         = var.region
  zone           = "${var.region}-a"
  network_id     = module.network.network_id
  subnet_id      = module.network.subnet_id
  project_id     = var.project_id
}
