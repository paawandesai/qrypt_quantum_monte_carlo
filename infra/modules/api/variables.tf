variable "name" {
  description = "Resource name prefix."
  type        = string
}

variable "environment" {
  description = "Environment name, passed to the function for metric dimensions."
  type        = string
}

variable "zip_dir" {
  description = "Directory holding api.zip."
  type        = string
}

variable "jobs_table_name" {
  description = "Jobs table name."
  type        = string
}

variable "jobs_table_arn" {
  description = "Jobs table ARN."
  type        = string
}

variable "shards_table_name" {
  description = "Shards table name (DescribeTable only, for /health)."
  type        = string
}

variable "shards_table_arn" {
  description = "Shards table ARN."
  type        = string
}

variable "shard_queue_url" {
  description = "Shard queue URL."
  type        = string
}

variable "shard_queue_arn" {
  description = "Shard queue ARN."
  type        = string
}

variable "dlq_url" {
  description = "DLQ URL (depth shown on /system)."
  type        = string
}

variable "dlq_arn" {
  description = "DLQ ARN."
  type        = string
}

variable "qrypt_key_param_name" {
  description = "SSM parameter name of the Qrypt token."
  type        = string
}

variable "qrypt_key_param_arn" {
  description = "SSM parameter ARN of the Qrypt token (IAM scoping only)."
  type        = string
}

variable "api_key_param_name" {
  description = "SSM parameter name of the API bearer key."
  type        = string
}

variable "api_key_param_arn" {
  description = "SSM parameter ARN of the API bearer key (IAM scoping only)."
  type        = string
}

variable "qrypt_entropy_url" {
  description = "Qrypt Entropy endpoint."
  type        = string
}

variable "reserved_concurrency" {
  description = "API reserved concurrency (the Function URL's only throttle)."
  type        = number
}

variable "max_paths" {
  description = "Per-job paths cap."
  type        = number
}

variable "max_shards" {
  description = "Per-job shards cap."
  type        = number
}

variable "max_paths_per_shard" {
  description = "Paths-per-shard cap."
  type        = number
}

variable "entropy_ceiling_kib" {
  description = "Total Qrypt KiB allowed."
  type        = number
}

variable "qrypt_rate_per_10s" {
  description = "Submissions per 10 s window."
  type        = number
}

variable "fault_injection" {
  description = "Accept fail_shards on submit."
  type        = bool
}

variable "log_retention_days" {
  description = "Log retention in days."
  type        = number
}

variable "memory_mb" {
  description = "API memory (MB)."
  type        = number
  default     = 512
}

variable "timeout_s" {
  description = "API timeout (s); covers the 5 s Qrypt timeout plus DynamoDB/SQS writes."
  type        = number
  default     = 15
}
