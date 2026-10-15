data "aws_caller_identity" "current" {}

locals {
  name       = "qmc-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id

  # SSM parameter ARNs built from names, only to scope IAM. There is deliberately
  # no data "aws_ssm_parameter": it decrypts by default and would put the secret
  # into state and plan output.
  qrypt_key_param_arn = "arn:aws:ssm:${var.region}:${local.account_id}:parameter${var.qrypt_key_param_name}"

  # The reconciler writes off a job's missing shards after this deadline, so it
  # must outlast every retry a shard can get, plus a margin for the last run.
  job_deadline_s    = var.max_receive_count * var.shard_visibility_timeout_s + 900
  api_key_param_arn = "arn:aws:ssm:${var.region}:${local.account_id}:parameter${var.api_key_param_name}"
}

module "data" {
  source = "./modules/data"
  name   = local.name
}

module "workers" {
  source = "./modules/workers"

  name                 = local.name
  environment          = var.environment
  zip_dir              = var.lambda_zip_dir
  jobs_table_name      = module.data.jobs_table_name
  jobs_table_arn       = module.data.jobs_table_arn
  shards_table_name    = module.data.shards_table_name
  shards_table_arn     = module.data.shards_table_arn
  reserved_concurrency = var.worker_reserved_concurrency
  max_concurrency      = var.worker_max_concurrency
  max_receive_count    = var.max_receive_count
  visibility_timeout_s = var.shard_visibility_timeout_s
  fault_injection      = var.enable_fault_injection
  log_retention_days   = var.log_retention_days
}

module "api" {
  source = "./modules/api"

  name                 = local.name
  environment          = var.environment
  zip_dir              = var.lambda_zip_dir
  jobs_table_name      = module.data.jobs_table_name
  jobs_table_arn       = module.data.jobs_table_arn
  shards_table_name    = module.data.shards_table_name
  shards_table_arn     = module.data.shards_table_arn
  shard_queue_url      = module.workers.shard_queue_url
  shard_queue_arn      = module.workers.shard_queue_arn
  dlq_url              = module.workers.dlq_url
  dlq_arn              = module.workers.dlq_arn
  qrypt_key_param_name = var.qrypt_key_param_name
  qrypt_key_param_arn  = local.qrypt_key_param_arn
  api_key_param_name   = var.api_key_param_name
  api_key_param_arn    = local.api_key_param_arn
  qrypt_entropy_url    = var.qrypt_entropy_url
  reserved_concurrency = var.api_reserved_concurrency
  max_paths            = var.max_paths
  max_shards           = var.max_shards
  max_paths_per_shard  = var.max_paths_per_shard
  entropy_ceiling_kib  = var.entropy_ceiling_kib
  qrypt_rate_per_10s   = var.qrypt_rate_per_10s
  job_deadline_s       = local.job_deadline_s
  fault_injection      = var.enable_fault_injection
  log_retention_days   = var.log_retention_days
}

module "observability" {
  source = "./modules/observability"

  name                 = local.name
  environment          = var.environment
  region               = var.region
  account_id           = local.account_id
  alarm_email          = var.alarm_email
  api_function_name    = module.api.function_name
  worker_function_name = module.workers.worker_function_name
  reconciler_function  = module.workers.reconciler_function_name
  dlq_name             = module.workers.dlq_name
  shard_queue_name     = module.workers.shard_queue_name
}

module "budget" {
  source = "./modules/budget"

  name          = local.name
  limit_usd     = var.budget_limit_usd
  sns_topic_arn = module.observability.alerts_topic_arn
  alarm_email   = var.alarm_email
}
