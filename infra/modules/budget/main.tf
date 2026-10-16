# A backstop, not a limiter: AWS Budgets evaluates spend a few times a day, so
# alerts arrive hours late and stop nothing. The real limiters are reserved
# concurrency, the per-job path caps and the DLQ maxReceiveCount.
resource "aws_budgets_budget" "monthly" {
  name         = "${var.name}-monthly"
  budget_type  = "COST"
  limit_amount = var.limit_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Count spend before credits and refunds: on a credit-funded account the net
  # cost stays $0 while credits drain, and the alert would never fire.
  cost_types {
    include_credit = false
    include_refund = false
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 1
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alarm_email]
    subscriber_sns_topic_arns  = [var.sns_topic_arn]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alarm_email]
    subscriber_sns_topic_arns  = [var.sns_topic_arn]
  }
}
