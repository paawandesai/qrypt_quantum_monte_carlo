output "jobs_table_name" {
  description = "Jobs table name."
  value       = aws_dynamodb_table.jobs.name
}

output "jobs_table_arn" {
  description = "Jobs table ARN."
  value       = aws_dynamodb_table.jobs.arn
}

output "shards_table_name" {
  description = "Shards table name."
  value       = aws_dynamodb_table.shards.name
}

output "shards_table_arn" {
  description = "Shards table ARN."
  value       = aws_dynamodb_table.shards.arn
}
