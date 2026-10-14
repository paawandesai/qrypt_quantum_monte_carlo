# Two provisioned-capacity tables. The always-free 25 RCU / 25 WCU is per account
# per region and GSIs count: jobs 5/5 + its GSI 5/5 + shards 10/10 = 20/20.
# Never PAY_PER_REQUEST.

resource "aws_dynamodb_table" "jobs" {
  #checkov:skip=CKV_AWS_119:AWS-owned key encryption at rest is on by default; a customer-managed KMS key costs ~$1/month and would break the $0 target.
  #checkov:skip=CKV2_AWS_16:Auto scaling would raise capacity past the always-free 25 RCU/WCU; fixed capacity is a cost limiter here.
  #checkov:skip=CKV_AWS_28:Point-in-time recovery bills per GB-month; job data is demo data purged by TTL after 7 days.
  name           = "${var.name}-jobs"
  billing_mode   = "PROVISIONED"
  read_capacity  = var.jobs_rcu
  write_capacity = var.jobs_wcu
  hash_key       = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "S"
  }

  global_secondary_index {
    name = "by_status"

    key_schema {
      attribute_name = "status"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "created_at"
      key_type       = "RANGE"
    }

    projection_type = "ALL"
    read_capacity   = var.gsi_rcu
    write_capacity  = var.gsi_wcu
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  server_side_encryption {
    enabled = false # false = AWS-owned key (still encrypted at rest, no KMS charge)
  }
}

resource "aws_dynamodb_table" "shards" {
  #checkov:skip=CKV_AWS_119:AWS-owned key encryption at rest is on by default; a customer-managed KMS key costs ~$1/month and would break the $0 target.
  #checkov:skip=CKV2_AWS_16:Auto scaling would raise capacity past the always-free 25 RCU/WCU; fixed capacity is a cost limiter here.
  #checkov:skip=CKV_AWS_28:Point-in-time recovery bills per GB-month; shard partials are demo data purged by TTL after 7 days.
  name           = "${var.name}-shards"
  billing_mode   = "PROVISIONED"
  read_capacity  = var.shards_rcu
  write_capacity = var.shards_wcu
  hash_key       = "job_id"
  range_key      = "shard_index"

  attribute {
    name = "job_id"
    type = "S"
  }

  attribute {
    name = "shard_index"
    type = "N"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  server_side_encryption {
    enabled = false
  }
}
