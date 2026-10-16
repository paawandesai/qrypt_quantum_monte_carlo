output "api_url" {
  description = "Function URL of the API (and the web console at /). Calls need the bearer key from SSM."
  value       = module.api.function_url
}

output "jobs_table" {
  description = "DynamoDB jobs table name."
  value       = module.data.jobs_table_name
}

output "shard_queue_url" {
  description = "SQS queue that fans shards out to workers."
  value       = module.workers.shard_queue_url
  # Queue URLs contain the AWS account id; sensitive keeps it out of apply
  # output and screen recordings (`terraform output -raw` still prints it).
  sensitive = true
}

output "dlq_url" {
  description = "Dead-letter queue for shards that failed max_receive_count times. Never consumed, so its depth alarm fires."
  value       = module.workers.dlq_url
  sensitive   = true
}

output "alerts_topic" {
  description = "SNS topic name for alarms and budget alerts."
  value       = module.observability.alerts_topic_name
}

output "dashboard_name" {
  description = "CloudWatch dashboard with job, entropy and worker metrics."
  value       = module.observability.dashboard_name
}

output "api_key_param_name" {
  description = "Where the bearer key lives. Fetch it with: aws ssm get-parameter --name <this> --with-decryption --query Parameter.Value --output text"
  value       = var.api_key_param_name
}
