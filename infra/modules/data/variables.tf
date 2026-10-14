variable "name" {
  description = "Resource name prefix."
  type        = string
}

variable "jobs_rcu" {
  description = "Provisioned read capacity for the jobs table."
  type        = number
  default     = 5
}

variable "jobs_wcu" {
  description = "Provisioned write capacity for the jobs table."
  type        = number
  default     = 5
}

variable "gsi_rcu" {
  description = "Provisioned read capacity for the jobs by_status GSI."
  type        = number
  default     = 5
}

variable "gsi_wcu" {
  description = "Provisioned write capacity for the jobs by_status GSI."
  type        = number
  default     = 5
}

variable "shards_rcu" {
  description = "Provisioned read capacity for the shards table."
  type        = number
  default     = 10
}

variable "shards_wcu" {
  description = "Provisioned write capacity for the shards table."
  type        = number
  default     = 10
}
