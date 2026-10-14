variable "name" {
  description = "Resource name prefix."
  type        = string
}

variable "limit_usd" {
  description = "Monthly budget in USD."
  type        = string
}

variable "sns_topic_arn" {
  description = "SNS topic that receives budget alerts."
  type        = string
}

variable "alarm_email" {
  description = "Email that receives budget alerts directly."
  type        = string
}
