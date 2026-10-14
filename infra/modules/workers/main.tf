# ---- Queues -----------------------------------------------------------------

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name}-shards-dlq"
  message_retention_seconds = 1209600 # 14 days
  receive_wait_time_seconds = 20
  sqs_managed_sse_enabled   = true # SSE-SQS: encrypted at rest, no KMS charge
}

resource "aws_sqs_queue" "shards" {
  name                       = "${var.name}-shards"
  visibility_timeout_seconds = var.visibility_timeout_s
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 20 # long polling
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.shards.arn]
  })
}

# ---- Worker -----------------------------------------------------------------

resource "aws_cloudwatch_log_group" "worker" {
  #checkov:skip=CKV_AWS_158:CloudWatch Logs encrypts at rest by default; a customer-managed KMS key costs ~$1/month.
  #checkov:skip=CKV_AWS_338:Retention is deliberately short (demo data purge, log storage cost); logs carry ids and status codes only.
  name              = "/aws/lambda/${var.name}-worker"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "worker" {
  name               = "${var.name}-worker"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid       = "ConsumeShardQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]
    resources = [aws_sqs_queue.shards.arn]
  }
  statement {
    sid       = "WriteAndReadShards"
    actions   = ["dynamodb:PutItem", "dynamodb:Query"]
    resources = [var.shards_table_arn]
  }
  statement {
    sid       = "UpdateJobs"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [var.jobs_table_arn]
  }
  statement {
    sid       = "OwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.worker.arn}:*"]
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = "worker"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

resource "aws_lambda_function" "worker" {
  #checkov:skip=CKV_AWS_117:No VPC: the function only calls AWS APIs; a VPC would need a NAT gateway or endpoints, neither free.
  #checkov:skip=CKV_AWS_116:Failed shard messages already go to the SQS DLQ via the queue's redrive policy (async DLQ does not apply to SQS event sources).
  #checkov:skip=CKV_AWS_173:Environment variables hold names and limits only, never secrets; a CMK would cost ~$1/month.
  #checkov:skip=CKV_AWS_272:Code signing needs AWS Signer profiles and adds a paid-tier dependency; zips are built reproducibly in CI instead.
  #checkov:skip=CKV_AWS_50:X-Ray tracing is off to keep the free-tier arithmetic simple; structured logs carry job_id and timings.
  function_name                  = "${var.name}-worker"
  role                           = aws_iam_role.worker.arn
  runtime                        = "python3.13"
  architectures                  = ["arm64"]
  handler                        = "qmc.handlers.worker.handler"
  filename                       = "${var.zip_dir}/worker.zip"
  source_code_hash               = filebase64sha256("${var.zip_dir}/worker.zip")
  memory_size                    = var.worker_memory_mb
  timeout                        = var.worker_timeout_s
  reserved_concurrent_executions = var.reserved_concurrency

  environment {
    variables = {
      ENVIRONMENT            = var.environment
      FUNCTION_ROLE          = "worker"
      JOBS_TABLE             = var.jobs_table_name
      SHARDS_TABLE           = var.shards_table_name
      MAX_RECEIVE_COUNT      = tostring(var.max_receive_count)
      ENABLE_FAULT_INJECTION = tostring(var.fault_injection)
      OPENBLAS_NUM_THREADS   = "1"
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.worker.name
  }

  depends_on = [aws_iam_role_policy.worker]
}

resource "aws_lambda_event_source_mapping" "shards" {
  event_source_arn        = aws_sqs_queue.shards.arn
  function_name           = aws_lambda_function.worker.arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = var.max_concurrency
  }
}

# ---- Reconciler (scheduled) ---------------------------------------------------

resource "aws_cloudwatch_log_group" "reconciler" {
  #checkov:skip=CKV_AWS_158:CloudWatch Logs encrypts at rest by default; a customer-managed KMS key costs ~$1/month.
  #checkov:skip=CKV_AWS_338:Retention is deliberately short (demo data purge, log storage cost); logs carry ids and status codes only.
  name              = "/aws/lambda/${var.name}-reconciler"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "reconciler" {
  name               = "${var.name}-reconciler"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

data "aws_iam_policy_document" "reconciler" {
  statement {
    sid       = "FinalizeJobs"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [var.jobs_table_arn, "${var.jobs_table_arn}/index/by_status"]
  }
  statement {
    sid       = "ReadShards"
    actions   = ["dynamodb:Query"]
    resources = [var.shards_table_arn]
  }
  statement {
    sid       = "OwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.reconciler.arn}:*"]
  }
}

resource "aws_iam_role_policy" "reconciler" {
  name   = "reconciler"
  role   = aws_iam_role.reconciler.id
  policy = data.aws_iam_policy_document.reconciler.json
}

resource "aws_lambda_function" "reconciler" {
  #checkov:skip=CKV_AWS_117:No VPC: the function only calls AWS APIs; a VPC would need a NAT gateway or endpoints, neither free.
  #checkov:skip=CKV_AWS_116:Scheduled and idempotent; a missed run is picked up by the next one five minutes later.
  #checkov:skip=CKV_AWS_173:Environment variables hold names only, never secrets; a CMK would cost ~$1/month.
  #checkov:skip=CKV_AWS_272:Code signing needs AWS Signer profiles; zips are built reproducibly in CI instead.
  #checkov:skip=CKV_AWS_50:X-Ray tracing is off to keep the free-tier arithmetic simple.
  #checkov:skip=CKV_AWS_115:Concurrency is bounded by the 5-minute schedule; reserving it would only consume account quota.
  function_name = "${var.name}-reconciler"
  role          = aws_iam_role.reconciler.arn
  runtime       = "python3.13"
  architectures = ["arm64"]
  handler       = "qmc.handlers.reconciler.handler"
  filename      = "${var.zip_dir}/reconciler.zip"

  source_code_hash = filebase64sha256("${var.zip_dir}/reconciler.zip")
  memory_size      = 256
  timeout          = 60

  environment {
    variables = {
      ENVIRONMENT   = var.environment
      FUNCTION_ROLE = "reconciler"
      JOBS_TABLE    = var.jobs_table_name
      SHARDS_TABLE  = var.shards_table_name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.reconciler.name
  }

  depends_on = [aws_iam_role_policy.reconciler]
}

resource "aws_cloudwatch_event_rule" "reconcile" {
  name                = "${var.name}-reconcile"
  description         = "Finalize jobs past their deadline (PARTIAL on missing shards)."
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "reconcile" {
  rule = aws_cloudwatch_event_rule.reconcile.name
  arn  = aws_lambda_function.reconciler.arn
}

resource "aws_lambda_permission" "reconcile" {
  statement_id  = "AllowEventBridgeSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.reconciler.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.reconcile.arn
}
