data "aws_caller_identity" "current" {}

locals {
  acct   = data.aws_caller_identity.current.account_id
  region = var.region
  p      = var.name_prefix
  # The only roles CI may create, edit or pass: one per function, per environment.
  function_roles = [for f in ["api", "worker", "reconciler"] : "arn:aws:iam::${local.acct}:role/${local.p}*-${f}"]
}

resource "aws_iam_openid_connect_provider" "gitlab" {
  url            = "https://${var.gitlab_host}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.gitlab.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${var.gitlab_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${var.gitlab_host}:sub"
      values   = ["project_path:${var.gitlab_project_path}:ref_type:branch:ref:${var.allowed_ref}"]
    }
  }
}

resource "aws_iam_role" "ci" {
  name                 = "qmc-gitlab-ci-deployer"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
}

# Deploy permissions scoped by resource-name prefix. Name-prefix patterns
# (qmc-*) are used instead of "*"; the only account-wide grants are two
# read-only lookups that AWS cannot scope (LogsDescribe, EventSourceMappingGetAfterDelete).
data "aws_iam_policy_document" "deploy" {
  #checkov:skip=CKV_AWS_356:Two read-only grants use Resource "*": lambda:GetEventSourceMapping (Lambda authorizes lookups of a deleted mapping against "*", so destroy cannot finish otherwise) and logs:DescribeLogGroups (a list call that takes no log-group ARN).
  statement {
    sid = "Lambda"
    actions = [
      "lambda:CreateFunction", "lambda:DeleteFunction", "lambda:GetFunction", "lambda:GetFunctionConfiguration",
      "lambda:UpdateFunctionCode", "lambda:UpdateFunctionConfiguration", "lambda:ListVersionsByFunction",
      "lambda:GetFunctionCodeSigningConfig", "lambda:PutFunctionConcurrency", "lambda:DeleteFunctionConcurrency",
      "lambda:GetFunctionConcurrency", "lambda:AddPermission", "lambda:RemovePermission", "lambda:GetPolicy",
      "lambda:CreateFunctionUrlConfig", "lambda:GetFunctionUrlConfig", "lambda:UpdateFunctionUrlConfig",
      "lambda:DeleteFunctionUrlConfig", "lambda:TagResource", "lambda:UntagResource", "lambda:ListTags",
    ]
    resources = ["arn:aws:lambda:${local.region}:${local.acct}:function:${local.p}*"]
  }
  statement {
    sid       = "EventSourceMappings"
    actions   = ["lambda:CreateEventSourceMapping"]
    resources = ["arn:aws:lambda:${local.region}:${local.acct}:event-source-mapping:*"]
    condition {
      test     = "ArnLike"
      variable = "lambda:FunctionArn"
      values   = ["arn:aws:lambda:${local.region}:${local.acct}:function:${local.p}*"]
    }
  }
  # Only CreateEventSourceMapping carries a function ARN for the condition above.
  # Get, update, delete and tagging address a mapping by its UUID, so they get
  # their own unconditioned statement (without it `terraform destroy` fails while
  # waiting for the mapping to go away).
  statement {
    sid = "EventSourceMappingsByUuid"
    actions = [
      "lambda:GetEventSourceMapping", "lambda:UpdateEventSourceMapping", "lambda:DeleteEventSourceMapping",
      "lambda:TagResource", "lambda:UntagResource", "lambda:ListTags",
    ]
    resources = ["arn:aws:lambda:${local.region}:${local.acct}:event-source-mapping:*"]
  }
  statement {
    sid = "Roles"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:TagRole", "iam:UntagRole", "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies", "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy",
      "iam:ListInstanceProfilesForRole", "iam:UpdateAssumeRolePolicy",
    ]
    resources = local.function_roles
  }
  statement {
    sid       = "PassRolesToLambdaOnly"
    actions   = ["iam:PassRole"]
    resources = local.function_roles
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com"]
    }
  }
  statement {
    sid = "DynamoDB"
    actions = [
      "dynamodb:CreateTable", "dynamodb:DeleteTable", "dynamodb:DescribeTable", "dynamodb:UpdateTable",
      "dynamodb:DescribeTimeToLive", "dynamodb:UpdateTimeToLive", "dynamodb:DescribeContinuousBackups",
      "dynamodb:ListTagsOfResource", "dynamodb:TagResource", "dynamodb:UntagResource",
    ]
    resources = ["arn:aws:dynamodb:${local.region}:${local.acct}:table/${local.p}*"]
  }
  statement {
    sid = "SQS"
    actions = [
      "sqs:CreateQueue", "sqs:DeleteQueue", "sqs:GetQueueAttributes", "sqs:SetQueueAttributes", "sqs:GetQueueUrl",
      "sqs:ListQueueTags", "sqs:TagQueue", "sqs:UntagQueue",
    ]
    resources = ["arn:aws:sqs:${local.region}:${local.acct}:${local.p}*"]
  }
  statement {
    sid = "Logs"
    actions = [
      "logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:PutRetentionPolicy", "logs:DeleteRetentionPolicy",
      "logs:DescribeLogGroups", "logs:ListTagsForResource", "logs:TagResource", "logs:UntagResource",
    ]
    resources = ["arn:aws:logs:${local.region}:${local.acct}:log-group:/aws/lambda/${local.p}*"]
  }
  # DescribeLogGroups is a list call: it does not accept a log-group ARN prefix,
  # so it is granted on all log groups in the region (read-only).
  statement {
    sid       = "LogsDescribe"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
  }
  # After deleting a mapping, Terraform polls GetEventSourceMapping until it is
  # gone, and Lambda authorizes a lookup of a missing mapping against "*", so a
  # scoped grant turns "deleted" into AccessDenied and destroy fails. Read-only.
  statement {
    sid       = "EventSourceMappingGetAfterDelete"
    actions   = ["lambda:GetEventSourceMapping"]
    resources = ["*"]
  }
  statement {
    sid = "S3Code"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:ListBucket",
      # Explicit bucket actions (no Get*/Put* wildcards): the reads the AWS
      # provider makes when refreshing a bucket, and only the two bucket writes
      # this stack needs. No PutBucketPolicy or PutBucketAcl, so CI cannot make a
      # bucket public.
      "s3:GetBucketAcl",
      "s3:GetBucketCORS",
      "s3:GetBucketLocation",
      "s3:GetBucketLogging",
      "s3:GetBucketNotification",
      "s3:GetBucketObjectLockConfiguration",
      "s3:GetBucketOwnershipControls",
      "s3:GetBucketPolicy",
      "s3:GetBucketPolicyStatus",
      "s3:GetBucketPublicAccessBlock",
      "s3:GetBucketRequestPayment",
      "s3:GetBucketTagging",
      "s3:GetBucketVersioning",
      "s3:GetBucketWebsite",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutBucketTagging",
      "s3:DeleteBucketPolicy",
      "s3:GetAccelerateConfiguration",
      "s3:GetEncryptionConfiguration",
      "s3:PutEncryptionConfiguration",
      "s3:GetLifecycleConfiguration",
      "s3:PutLifecycleConfiguration",
      "s3:GetReplicationConfiguration",
      "s3:PutObject",
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:GetObjectTagging",
      "s3:DeleteObject",
      "s3:DeleteObjectVersion",
      "s3:ListBucketVersions",
    ]
    resources = ["arn:aws:s3:::${local.p}*", "arn:aws:s3:::${local.p}*/*"]
  }
  statement {
    sid = "EventBridge"
    actions = [
      "events:PutRule", "events:DeleteRule", "events:DescribeRule", "events:PutTargets", "events:RemoveTargets",
      "events:ListTargetsByRule", "events:ListTagsForResource", "events:TagResource", "events:UntagResource",
    ]
    resources = ["arn:aws:events:${local.region}:${local.acct}:rule/${local.p}*"]
  }
  statement {
    sid = "SNS"
    actions = [
      "sns:CreateTopic", "sns:DeleteTopic", "sns:GetTopicAttributes", "sns:SetTopicAttributes", "sns:Subscribe",
      "sns:Unsubscribe", "sns:GetSubscriptionAttributes", "sns:ListSubscriptionsByTopic",
      "sns:ListTagsForResource", "sns:TagResource", "sns:UntagResource",
    ]
    resources = ["arn:aws:sns:${local.region}:${local.acct}:${local.p}*"]
  }
  statement {
    sid = "CloudWatch"
    actions = [
      "cloudwatch:PutMetricAlarm", "cloudwatch:DeleteAlarms", "cloudwatch:DescribeAlarms",
      "cloudwatch:ListTagsForResource", "cloudwatch:TagResource", "cloudwatch:UntagResource",
      "cloudwatch:PutDashboard", "cloudwatch:GetDashboard", "cloudwatch:DeleteDashboards",
    ]
    resources = [
      "arn:aws:cloudwatch:${local.region}:${local.acct}:alarm:${local.p}*",
      "arn:aws:cloudwatch::${local.acct}:dashboard/${local.p}*",
    ]
  }
  statement {
    sid = "Budgets"
    actions = [
      "budgets:ViewBudget", "budgets:ModifyBudget", "budgets:ListTagsForResource", "budgets:TagResource",
      "budgets:UntagResource",
    ]
    resources = ["arn:aws:budgets::${local.acct}:budget/${local.p}*"]
  }
}

# The deploy policy may create and edit roles only under the three function role
# names, and the CI role sits outside them; this explicit deny also stops it from
# editing its own permissions or trust policy, whatever the allows above say.
resource "aws_iam_role_policy" "deny_self_edit" {
  name = "deny-self-edit"
  role = aws_iam_role.ci.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "NoSelfEdit"
      Effect = "Deny"
      Action = [
        "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:AttachRolePolicy", "iam:DetachRolePolicy",
        "iam:UpdateAssumeRolePolicy", "iam:PutRolePermissionsBoundary", "iam:DeleteRolePermissionsBoundary",
        "iam:UpdateRole", "iam:DeleteRole", "iam:TagRole", "iam:UntagRole",
      ]
      Resource = aws_iam_role.ci.arn
    }]
  })
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy-qmc-stack"
  role   = aws_iam_role.ci.id
  policy = data.aws_iam_policy_document.deploy.json
}
