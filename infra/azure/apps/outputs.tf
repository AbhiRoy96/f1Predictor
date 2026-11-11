output "storage_name" {
  description = "Foundation storage account used for pre-release snapshots"
  value       = var.storage_account_name
}

output "init_job_name" {
  description = "Manual database initialization job"
  value       = azapi_resource.init.name
}
