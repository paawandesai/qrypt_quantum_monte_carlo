variable "name" {
  description = "Resource name prefix."
  type        = string
}

variable "environment" {
  description = "Environment name, passed to functions for metric dimensions."
  type        = string
}

variable "zip_dir" {
  description = "Directory holding worker.zip and reconciler.zip."
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
  description = "Shards table name."
  type        = string
}

variable "shards_table_arn" {
  description = "Shards table ARN."
  type        = string
}

variable "reserved_concurrency" {
  description = "Worker reserved concurrency."
  type        = number
}

variable "max_concurrency" {
  description = "maximum_concurrency on the SQS event-source mapping."
  type        = number
}

variable "max_receive_count" {
  description = "Deliveries before a shard message moves to the DLQ."
  type        = number
}

variable "fault_injection" {
  description = "Enable the fail_shards test path."
  type        = bool
}

variable "log_retention_days" {
  description = "Log retention in days."
  type        = number
}

variable "worker_memory_mb" {
  description = "Worker memory (MB); CPU scales with it."
  type        = number
  default     = 1024
}

variable "worker_timeout_s" {
  description = "Worker timeout (s). Shards are capped so they finish far below it."
  type        = number
  default     = 30
}

variable "visibility_timeout_s" {
  description = "Shard queue visibility timeout (s). AWS recommends at least 6x the worker timeout for an SQS event source, so a throttled retry does not use up a receive."
  type        = number
}
