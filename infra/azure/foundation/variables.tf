variable "resource_group_name" {
  description = "Existing staging resource group"
  type        = string
}

variable "prefix" {
  description = "Short lowercase resource prefix"
  type        = string
  default     = "gridcast"
}

variable "bootstrap_key_vault_id" {
  description = "Existing vault containing gridcast-database-password, gridcast-proxy-token and gridcast-entra-client-secret"
  type        = string
}

variable "budget_contact_emails" {
  description = "Monthly budget notification recipients"
  type        = list(string)
}

variable "budget_start_date" {
  description = "First day of the current month in ISO UTC format"
  type        = string
}

variable "monthly_budget" {
  description = "Monthly resource group budget in the billing currency"
  type        = number
  default     = 100
}

variable "daily_log_cap" {
  description = "Log Analytics daily ingestion cap in GiB"
  type        = number
  default     = 1
}

variable "log_retention_days" {
  description = "Log Analytics retention in days"
  type        = number
  default     = 30
}

variable "database_backup_days" {
  description = "PostgreSQL point-in-time restore retention in days"
  type        = number
  default     = 14
}
