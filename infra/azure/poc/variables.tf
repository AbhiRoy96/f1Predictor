variable "resource_group_name" {
  description = "Existing dedicated PoC resource group, never the staging resource group"
  type        = string
}

variable "prefix" {
  description = "Short lowercase resource prefix for this isolated PoC"
  type        = string
  default     = "gridcast-minpoc"
}

variable "database_password" {
  description = "Strong PostgreSQL password supplied through TF_VAR_database_password, not committed files"
  type        = string
  sensitive   = true
}

variable "release_tag" {
  description = "Immutable tag already pushed for gridcast-api and gridcast-web images"
  type        = string
}

variable "initialized_release_tag" {
  description = "Set to release_tag only after that image's db-init job execution succeeds"
  type        = string
  default     = null
}

variable "is_public_poc" {
  description = "Explicit acknowledgement that this disposable PoC exposes an unauthenticated public web app"
  type        = bool
  default     = false
}
