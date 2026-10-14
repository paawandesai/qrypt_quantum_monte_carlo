resource "aws_sns_topic" "alerts" {
  #checkov:skip=CKV_AWS_26:CloudWatch alarms and AWS Budgets cannot publish to a topic encrypted with the AWS-managed SNS key, and a CMK costs ~$1/month. Messages carry alarm names only.
  name = "${var.name}-alerts"
}

data "aws_iam_policy_document" "alerts" {
  statement {
    sid       = "CloudWatchAlarms"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
  statement {
    sid       = "Budgets"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts.json
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  dims = { Environment = var.environment }
  alarms = {
    worker-errors = {
      description = "A worker invocation failed (a shard will be retried, then sent to the DLQ)."
      namespace   = "AWS/Lambda"
      metric      = "Errors"
      statistic   = "Sum"
      dimensions  = { FunctionName = var.worker_function_name }
    }
    dlq-depth = {
      description = "A shard exhausted its retries and is in the dead-letter queue; its job is PARTIAL."
      namespace   = "AWS/SQS"
      metric      = "ApproximateNumberOfMessagesVisible"
      statistic   = "Maximum"
      dimensions  = { QueueName = var.dlq_name }
    }
    api-errors = {
      description = "The API function errored (unhandled exception or timeout)."
      namespace   = "AWS/Lambda"
      metric      = "Errors"
      statistic   = "Sum"
      dimensions  = { FunctionName = var.api_function_name }
    }
    qrypt-failures = {
      description = "Qrypt entropy requests failed; affected jobs were rejected (fail closed)."
      namespace   = "QryptMC"
      metric      = "QryptFailures"
      statistic   = "Sum"
      dimensions  = merge(local.dims, { Function = "api" })
    }
    shard-failures = {
      description = "A shard failed on its last allowed attempt."
      namespace   = "QryptMC"
      metric      = "ShardFailures"
      statistic   = "Sum"
      dimensions  = merge(local.dims, { Function = "worker" })
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each = local.alarms

  alarm_name          = "${var.name}-${each.key}"
  alarm_description   = each.value.description
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.value.statistic
  dimensions          = each.value.dimensions
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.name}-overview"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title  = "Jobs and entropy"
          region = var.region
          stat   = "Sum"
          period = 300
          metrics = [
            ["QryptMC", "JobsSubmitted", "Environment", var.environment, "Function", "api"],
            ["QryptMC", "EntropyKiBConsumed", "Environment", var.environment, "Function", "api"],
            ["QryptMC", "QryptFailures", "Environment", var.environment, "Function", "api"],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title  = "Paths simulated and shard failures"
          region = var.region
          stat   = "Sum"
          period = 300
          metrics = [
            ["QryptMC", "PathsSimulated", "Environment", var.environment, "Function", "worker"],
            ["QryptMC", "ShardFailures", "Environment", var.environment, "Function", "worker", { yAxis = "right" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title  = "Lambda duration p95 (ms)"
          region = var.region
          stat   = "p95"
          period = 300
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", var.api_function_name],
            ["AWS/Lambda", "Duration", "FunctionName", var.worker_function_name],
            ["AWS/Lambda", "Duration", "FunctionName", var.reconciler_function],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title  = "Queues"
          region = var.region
          stat   = "Maximum"
          period = 60
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.shard_queue_name],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.dlq_name],
          ]
        }
      },
    ]
  })
}
