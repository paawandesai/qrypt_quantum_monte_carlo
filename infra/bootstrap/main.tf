data "aws_caller_identity" "current" {}

locals {
  acct   = data.aws_caller_identity.current.account_id
  region = var.region
  p      = var.name_prefix
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
# (qmc-*) are used instead of "*"; nothing here grants account-wide access.
data "aws_iam_policy_document" "deploy" {
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
    sid = "EventSourceMappings"
    actions = [
      "lambda:CreateEventSourceMapping", "lambda:GetEventSourceMapping", "lambda:UpdateEventSourceMapping",
      "lambda:DeleteEventSourceMapping", "lambda:TagResource", "lambda:ListTags",
    ]
    resources = ["arn:aws:lambda:${local.region}:${local.acct}:event-source-mapping:*"]
    condition {
      test     = "ArnLike"
      variable = "lambda:FunctionArn"
      values   = ["arn:aws:lambda:${local.region}:${local.acct}:function:${local.p}*"]
    }
  }
  statement {
    sid = "Roles"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:TagRole", "iam:UntagRole", "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies", "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy",
      "iam:ListInstanceProfilesForRole", "iam:UpdateAssumeRolePolicy",
    ]
    resources = ["arn:aws:iam::${local.acct}:role/${local.p}*"]
  }
  statement {
    sid       = "PassRolesToLambdaOnly"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.acct}:role/${local.p}*"]
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

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy-qmc-stack"
  role   = aws_iam_role.ci.id
  policy = data.aws_iam_policy_document.deploy.json
}
