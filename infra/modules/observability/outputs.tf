output "alerts_topic_arn" {
  description = "SNS topic ARN for alarms and budget alerts."
  value       = aws_sns_topic.alerts.arn
}

output "alerts_topic_name" {
  description = "SNS topic name."
  value       = aws_sns_topic.alerts.name
}

output "dashboard_name" {
  description = "CloudWatch dashboard name."
  value       = aws_cloudwatch_dashboard.main.dashboard_name
}
