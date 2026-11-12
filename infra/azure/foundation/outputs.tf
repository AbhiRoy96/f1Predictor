output "registry_name" {
  description = "Registry name for remote linux/amd64 builds"
  value       = azurerm_container_registry.main.name
}

output "environment_name" {
  description = "Managed Container Apps environment name"
  value       = azurerm_container_app_environment.main.name
}

output "vault_name" {
  description = "Runtime Key Vault name without secret values"
  value       = azurerm_key_vault.main.name
}

output "storage_name" {
  description = "Storage account name for snapshot operations"
  value       = azurerm_storage_account.main.name
}

output "postgres_name" {
  description = "Private PostgreSQL server name for restore drills"
  value       = azurerm_postgresql_flexible_server.main.name
}

output "entra_redirect_uri" {
  description = "Register this callback on the Entra app before enabling external web ingress"
  value       = "https://${var.prefix}-web.${azurerm_container_app_environment.main.default_domain}/.auth/login/aad/callback"
}
