output "shard_queue_url" {
  description = "Shard queue URL."
  value       = aws_sqs_queue.shards.id
}

output "shard_queue_arn" {
  description = "Shard queue ARN."
  value       = aws_sqs_queue.shards.arn
}

output "shard_queue_name" {
  description = "Shard queue name."
  value       = aws_sqs_queue.shards.name
}

output "dlq_url" {
  description = "DLQ URL."
  value       = aws_sqs_queue.dlq.id
}

output "dlq_arn" {
  description = "DLQ ARN."
  value       = aws_sqs_queue.dlq.arn
}

output "dlq_name" {
  description = "DLQ name."
  value       = aws_sqs_queue.dlq.name
}

output "worker_function_name" {
  description = "Worker function name."
  value       = aws_lambda_function.worker.function_name
}

output "reconciler_function_name" {
  description = "Reconciler function name."
  value       = aws_lambda_function.reconciler.function_name
}
