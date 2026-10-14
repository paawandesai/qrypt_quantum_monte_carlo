output "function_name" {
  description = "API function name."
  value       = aws_lambda_function.api.function_name
}

output "function_url" {
  description = "Public Function URL."
  value       = aws_lambda_function_url.api.function_url
}
