output "ci_role_arn" {
  description = "Set this as the masked GitLab CI/CD variable AWS_ROLE_ARN."
  value       = aws_iam_role.ci.arn
  sensitive   = true # contains the account id
}
