variable "region" {
  description = "AWS region for every resource."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name; prefixes every resource (qmc-<environment>-...). Per-branch stacks use the branch slug."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z0-9-]{1,20}$", var.environment))
    error_message = "environment must be 1-20 lowercase letters, digits or hyphens."
  }
}

variable "alarm_email" {
  description = "Email address subscribed to the alarm/budget SNS topic. AWS sends a confirmation email that must be accepted."
  type        = string
}

variable "qrypt_key_param_name" {
  description = "Name of the SSM SecureString holding the Qrypt token. Created out of band; Terraform only references the name."
  type        = string
  default     = "/qrypt-mc/qrypt-api-key"
}

variable "api_key_param_name" {
  description = "Name of the SSM SecureString holding the API bearer key. Created out of band; Terraform only references the name."
  type        = string
  default     = "/qrypt-mc/api-key"
}

variable "lambda_zip_dir" {
  description = "Directory containing api.zip, worker.zip and reconciler.zip from scripts/build.py."
  type        = string
  default     = "../build"
}

variable "worker_reserved_concurrency" {
  description = "Reserved concurrency for the worker. Caps parallel spend; requires the account quota to leave 100 unreserved. -1 = no reservation (temporary exception only while the account quota is below 115; see README Known constraints)."
  type        = number
  default     = 10

  validation {
    condition     = var.worker_reserved_concurrency == -1 || var.worker_reserved_concurrency >= 1
    error_message = "worker_reserved_concurrency must be -1 (no reservation) or >= 1."
  }
}

variable "api_reserved_concurrency" {
  description = "Reserved concurrency for the API. The only throttle a Function URL has (max ~10x this in requests/second). -1 = no reservation (temporary exception only; see README)."
  type        = number
  default     = 5

  validation {
    condition     = var.api_reserved_concurrency == -1 || var.api_reserved_concurrency >= 1
    error_message = "api_reserved_concurrency must be -1 (no reservation) or >= 1."
  }
}

variable "worker_max_concurrency" {
  description = "maximum_concurrency on the SQS event-source mapping (2-1000). Second guard after reserved concurrency."
  type        = number
  default     = 5
}

variable "max_paths" {
  description = "Largest paths value a job may request."
  type        = number
  default     = 10000000
}

variable "max_shards" {
  description = "Largest shards value a job may request."
  type        = number
  default     = 100
}

variable "max_paths_per_shard" {
  description = "Upper bound on paths per shard, so a worker finishes well under its timeout."
  type        = number
  default     = 2000000
}

variable "entropy_ceiling_kib" {
  description = "Total Qrypt KiB this deployment may consume before submit is refused (assessment budget is 10,240 KiB)."
  type        = number
  default     = 5000
}

variable "qrypt_rate_per_10s" {
  description = "Submissions per fixed 10 s window. 14 keeps any 10 s span at or under 28, below Qrypt's 30."
  type        = number
  default     = 14
}

variable "qrypt_entropy_url" {
  description = "Qrypt Entropy endpoint (checked against docs.qrypt.com/eaas before deploying)."
  type        = string
  default     = "https://api-eus.qrypt.com/api/v1/quantum-entropy"
}

variable "max_receive_count" {
  description = "Deliveries of a shard message before it moves to the DLQ."
  type        = number
  default     = 3
}

variable "enable_fault_injection" {
  description = "Test path in deployed code: when true, a job may name shard indexes that fail on purpose (for the DLQ alarm demo). Keep false otherwise."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for every function."
  type        = number
  default     = 14
}

variable "budget_limit_usd" {
  description = "Monthly AWS Budget amount in USD."
  type        = string
  default     = "1"
}
