output "budget_name" {
  description = "AWS Budget name."
  value       = aws_budgets_budget.monthly.name
}
