output "registry_name" {
  description = "Basic ACR name used to push both application images"
  value       = azurerm_container_registry.main.name
}

output "registry_login_server" {
  description = "Registry hostname for tagging and pushing local images"
  value       = azurerm_container_registry.main.login_server
}

output "environment_name" {
  description = "Shared default-network Container Apps environment"
  value       = azurerm_container_app_environment.main.name
}

output "storage_name" {
  description = "Storage account containing the sole postgres-data file share"
  value       = azurerm_storage_account.main.name
}

output "init_job_name" {
  description = "Migration job to run and wait for before application deployment"
  value       = azapi_resource.init.name
}

output "web_url" {
  description = "Public HTTPS URL with no sign-in protection, for disposable PoC use only"
  value       = "https://${azapi_resource.app["web"].name}.${azurerm_container_app_environment.main.default_domain}"
}
