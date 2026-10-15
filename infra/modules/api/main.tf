resource "aws_cloudwatch_log_group" "api" {
  #checkov:skip=CKV_AWS_158:CloudWatch Logs encrypts at rest by default; a customer-managed KMS key costs ~$1/month.
  #checkov:skip=CKV_AWS_338:Retention is deliberately short (demo data purge, log storage cost); logs carry ids and status codes only.
  name              = "/aws/lambda/${var.name}-api"
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

resource "aws_iam_role" "api" {
  name               = "${var.name}-api"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

data "aws_iam_policy_document" "api" {
  statement {
    sid     = "JobsTable"
    actions = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [
      var.jobs_table_arn,
      "${var.jobs_table_arn}/index/by_status",
    ]
  }
  statement {
    sid       = "HealthDescribeTables"
    actions   = ["dynamodb:DescribeTable"]
    resources = [var.jobs_table_arn, var.shards_table_arn]
  }
  statement {
    sid       = "EnqueueShards"
    actions   = ["sqs:SendMessage"]
    resources = [var.shard_queue_arn]
  }
  statement {
    sid       = "ReadDlqDepth"
    actions   = ["sqs:GetQueueAttributes"]
    resources = [var.dlq_arn]
  }
  statement {
    # Both SecureStrings use the AWS-managed alias/aws/ssm key, whose key policy
    # already allows decryption through SSM for this account; no kms:Decrypt grant is needed.
    sid       = "ReadKeys"
    actions   = ["ssm:GetParameter"]
    resources = [var.qrypt_key_param_arn, var.api_key_param_arn]
  }
  statement {
    sid       = "OwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "api"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_lambda_function" "api" {
  #checkov:skip=CKV_AWS_117:No VPC: the function calls AWS APIs and Qrypt over the internet; a VPC would need a NAT gateway, which is not free.
  #checkov:skip=CKV_AWS_116:Synchronous Function URL invocations return errors to the caller; an async DLQ never applies.
  #checkov:skip=CKV_AWS_173:Environment variables hold parameter names and limits, never secret values; a CMK would cost ~$1/month.
  #checkov:skip=CKV_AWS_272:Code signing needs AWS Signer profiles; zips are built reproducibly in CI instead.
  #checkov:skip=CKV_AWS_50:X-Ray tracing is off to keep the free-tier arithmetic simple; structured logs carry request ids and timings.
  function_name                  = "${var.name}-api"
  role                           = aws_iam_role.api.arn
  runtime                        = "python3.13"
  architectures                  = ["arm64"]
  handler                        = "qmc.handlers.api.handler"
  filename                       = "${var.zip_dir}/api.zip"
  source_code_hash               = filebase64sha256("${var.zip_dir}/api.zip")
  memory_size                    = var.memory_mb
  timeout                        = var.timeout_s
  reserved_concurrent_executions = var.reserved_concurrency

  environment {
    variables = {
      ENVIRONMENT            = var.environment
      FUNCTION_ROLE          = "api"
      JOBS_TABLE             = var.jobs_table_name
      SHARDS_TABLE           = var.shards_table_name
      SHARD_QUEUE_URL        = var.shard_queue_url
      DLQ_URL                = var.dlq_url
      QRYPT_KEY_PARAM        = var.qrypt_key_param_name # a name, not the token
      API_KEY_PARAM          = var.api_key_param_name   # a name, not the key
      QRYPT_ENTROPY_URL      = var.qrypt_entropy_url
      QRYPT_TIMEOUT_S        = "5"
      QRYPT_RATE_PER_10S     = tostring(var.qrypt_rate_per_10s)
      ENTROPY_CEILING_KIB    = tostring(var.entropy_ceiling_kib)
      MAX_PATHS              = tostring(var.max_paths)
      MAX_SHARDS             = tostring(var.max_shards)
      MAX_PATHS_PER_SHARD    = tostring(var.max_paths_per_shard)
      JOB_DEADLINE_S         = tostring(var.job_deadline_s)
      ENABLE_FAULT_INJECTION = tostring(var.fault_injection)
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.api.name
  }

  depends_on = [aws_iam_role_policy.api]
}

resource "aws_lambda_function_url" "api" {
  #checkov:skip=CKV_AWS_258:Public URL by design for the browser console; every route except the static console requires a bearer key checked in the handler (constant-time compare). This is an application-layer key check on a public endpoint: unauthenticated calls still invoke and bill the function, bounded by reserved concurrency.
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "NONE"
}

# AuthType NONE requires a resource policy allowing Principal "*". Both statements
# are scoped with conditions so they only apply through the Function URL. They are
# managed here so `terraform destroy` removes them (deleting a URL alone does not).
resource "aws_lambda_permission" "url_invoke" {
  #checkov:skip=CKV_AWS_301:Required by AWS for a Function URL with AuthType NONE (documented wildcard principal); scoped to Function URL invocations only, and the handler enforces the bearer key.
  statement_id           = "FunctionURLAllowPublicAccess"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.api.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "url_invoke_function" {
  #checkov:skip=CKV_AWS_301:Required by AWS for a Function URL with AuthType NONE (documented wildcard principal); scoped to Function URL invocations only, and the handler enforces the bearer key.
  statement_id             = "FunctionURLInvokeAllowPublicAccess"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.api.function_name
  principal                = "*"
  invoked_via_function_url = true
}
