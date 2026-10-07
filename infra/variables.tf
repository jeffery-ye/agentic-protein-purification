variable "region" {
  type    = string
  default = "us-east-1"
}

variable "domain" {
  type    = string
  default = "app.example.org"
}

variable "github_repository" {
  description = "owner/name of the repository whose main branch may deploy."
  type        = string
  default     = "OWNER/REPO"
}

variable "ami_id" {
  description = "Pinned Amazon Linux 2023 arm64 AMI. Pinned so an unrelated apply never replaces the instance."
  type        = string
}

variable "entrez_email" {
  description = "NCBI contact address, the initial value of /ppr/ENTREZ_EMAIL. Set in the gitignored terraform.tfvars."
  type        = string
}

variable "alert_email" {
  description = "Receives the uptime alarm and the budget alerts. Set in the gitignored terraform.tfvars."
  type        = string
}

variable "monthly_budget_usd" {
  type    = number
  default = 35
}

variable "cognito_domain_prefix" {
  description = "Prefix of Cognito's managed login domain, <prefix>.auth.<region>.amazoncognito.com. Unique per region across all AWS accounts."
  type        = string
  default     = "your-cognito-prefix"
}

variable "reviewer_username" {
  description = "The shared reviewer account (#25), whose profile page hides the change-password link."
  type        = string
  default     = "reviewer"
}
