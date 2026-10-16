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
      # The handler catches a shard's exception to report a partial batch failure,
      # so Lambda's Errors only counts crashes and timeouts; WorkerErrors (EMF)
      # counts every failed attempt. The alarm watches their sum.
      description = "A worker attempt failed (a shard will be retried, then sent to the DLQ), or the worker crashed or timed out."
      namespace   = "AWS/Lambda"
      metric      = "Errors"
      statistic   = "Sum"
      dimensions  = { FunctionName = var.worker_function_name }
      extra = {
        namespace  = "QryptMC"
        metric     = "WorkerErrors"
        dimensions = merge(local.dims, { Function = "worker" })
      }
    }
    dlq-depth = {
      description = "A shard exhausted its retries and is in the dead-letter queue; its job is PARTIAL."
      namespace   = "AWS/SQS"
      metric      = "ApproximateNumberOfMessagesVisible"
      statistic   = "Maximum"
      dimensions  = { QueueName = var.dlq_name }
      extra       = null
    }
    api-errors = {
      # The handler turns any unexpected exception into a 500 response, which
      # Lambda's Errors never counts; ApiErrors (EMF) counts those and API key
      # load failures. Fail-closed Qrypt 503/504s are expected and have their
      # own alarm, so Url5xxCount (which counts them too) is not used.
      description = "The API hit an unexpected error (500) or could not load its key, or the function crashed or timed out."
      namespace   = "AWS/Lambda"
      metric      = "Errors"
      statistic   = "Sum"
      dimensions  = { FunctionName = var.api_function_name }
      extra = {
        namespace  = "QryptMC"
        metric     = "ApiErrors"
        dimensions = merge(local.dims, { Function = "api" })
      }
    }
    qrypt-failures = {
      description = "Qrypt entropy requests failed; affected jobs were rejected (fail closed)."
      namespace   = "QryptMC"
      metric      = "QryptFailures"
      statistic   = "Sum"
      dimensions  = merge(local.dims, { Function = "api" })
      extra       = null
    }
    shard-failures = {
      description = "A shard failed on its last allowed attempt."
      namespace   = "QryptMC"
      metric      = "ShardFailures"
      statistic   = "Sum"
      dimensions  = merge(local.dims, { Function = "worker" })
      extra       = null
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each = local.alarms

  alarm_name        = "${var.name}-${each.key}"
  alarm_description = each.value.description
  # One metric: a plain alarm. Two metrics: a math alarm on their sum, with
  # missing data as 0 so either one alone can trip it. Any failing minute among
  # the last 3 trips it, because EMF metrics land after a delay and a 1-of-1
  # alarm (worse with FILL, which turns the not-yet-arrived minute into 0) could
  # look past them; it also keeps one ALARM/OK pair per incident rather than one
  # per retry.
  namespace           = each.value.extra == null ? each.value.namespace : null
  metric_name         = each.value.extra == null ? each.value.metric : null
  statistic           = each.value.extra == null ? each.value.statistic : null
  dimensions          = each.value.extra == null ? each.value.dimensions : null
  period              = each.value.extra == null ? 60 : null
  evaluation_periods  = 3
  datapoints_to_alarm = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  dynamic "metric_query" {
    for_each = each.value.extra == null ? [] : [
      { id = "m1", namespace = each.value.namespace, metric = each.value.metric, dimensions = each.value.dimensions },
      { id = "m2", namespace = each.value.extra.namespace, metric = each.value.extra.metric, dimensions = each.value.extra.dimensions },
    ]
    content {
      id          = metric_query.value.id
      return_data = false
      metric {
        namespace   = metric_query.value.namespace
        metric_name = metric_query.value.metric
        dimensions  = metric_query.value.dimensions
        stat        = "Sum"
        period      = 60
      }
    }
  }

  dynamic "metric_query" {
    for_each = each.value.extra == null ? [] : [1]
    content {
      id          = "total"
      expression  = "FILL(m1, 0) + FILL(m2, 0)"
      label       = "${each.value.metric} + ${each.value.extra.metric}"
      return_data = true
    }
  }
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
