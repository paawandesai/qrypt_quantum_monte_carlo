variable "region" {
  description = "Region of the app stack."
  type        = string
  default     = "us-east-1"
}

variable "gitlab_host" {
  description = "GitLab instance host that issues the CI id_tokens."
  type        = string
  default     = "labs.gauntletai.com"
}

variable "gitlab_project_path" {
  description = "GitLab project path allowed to assume the role."
  type        = string
  default     = "paawandesai/qrypt_quantum_monte_carlo"
}

variable "allowed_ref" {
  description = "Branch whose pipelines may assume the role (protected default branch)."
  type        = string
  default     = "main"
}

variable "name_prefix" {
  description = "Resource name prefix the app stack uses (qmc-<environment>)."
  type        = string
  default     = "qmc-"
}
