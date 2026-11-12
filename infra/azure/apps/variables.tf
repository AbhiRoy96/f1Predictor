variable "resource_group_name" {
  description = "Existing foundation resource group"
  type        = string
}

variable "prefix" {
  description = "Resource prefix used by the foundation"
  type        = string
  default     = "gridcast"
}

variable "registry_name" {
  description = "Registry name from the foundation output"
  type        = string
}

variable "vault_name" {
  description = "Vault name from the foundation output"
  type        = string
}

variable "storage_account_name" {
  description = "Storage account name from the foundation output for pre-release snapshots"
  type        = string
}

variable "release_tag" {
  description = "Immutable source SHA tagging both application images"
  type        = string
}

variable "revision_suffix" {
  description = "Fresh revision suffix for this release or rollback"
  type        = string
}

variable "entra_client_id" {
  description = "Single-tenant Entra web application client ID"
  type        = string
}

variable "entra_tenant_id" {
  description = "Entra tenant issuing web application tokens"
  type        = string
}

variable "otlp_endpoint" {
  description = "Controlled HTTP/protobuf OTLP endpoint or null to disable telemetry"
  type        = string
  default     = null
}

variable "scheduled_forecasts" {
  description = "Per-event enqueue-only schedules with genuine observed publication timestamps"
  type = list(object({
    name                    = string
    cron                    = string
    year                    = number
    event                   = string
    phase                   = string
    qualifying_completed_at = string
    race_start              = string
    grid_published_at       = optional(string)
  }))
  default = []
}
