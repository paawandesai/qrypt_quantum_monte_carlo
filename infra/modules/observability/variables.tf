variable "name" {
  description = "Resource name prefix."
  type        = string
}

variable "environment" {
  description = "Environment dimension value used by the custom metrics."
  type        = string
}

variable "region" {
  description = "Region for dashboard widgets."
  type        = string
}

variable "account_id" {
  description = "Account id for aws:SourceAccount conditions on the SNS topic policy."
  type        = string
}

variable "alarm_email" {
  description = "Email subscribed to alerts."
  type        = string
}

variable "api_function_name" {
  description = "API function name."
  type        = string
}

variable "worker_function_name" {
  description = "Worker function name."
  type        = string
}

variable "reconciler_function" {
  description = "Reconciler function name."
  type        = string
}

variable "dlq_name" {
  description = "DLQ name."
  type        = string
}

variable "shard_queue_name" {
  description = "Shard queue name."
  type        = string
}
