mock_provider "azurerm" {
  mock_data "azurerm_resource_group" {
    defaults = {
      id       = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/staging"
      location = "eastus"
    }
  }
  mock_data "azurerm_key_vault_secret" {
    defaults = { value = "test password+with/@?characters-long" }
  }
  mock_data "azurerm_client_config" {
    defaults = { tenant_id = "00000000-0000-0000-0000-000000000001" }
  }
}

mock_provider "azapi" {}

variables {
  resource_group_name    = "staging"
  bootstrap_key_vault_id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/staging/providers/Microsoft.KeyVault/vaults/bootstrap"
  budget_contact_emails  = ["operator@example.invalid"]
  budget_start_date      = "2026-11-01T00:00:00Z"
}

run "database_credentials" {
  command = plan
  assert {
    condition     = local.database_password_uri == "test%20password%2Bwith%2F%40%3Fcharacters-long"
    error_message = "Database credentials must be URI encoded, including spaces and literal plus signs"
  }
}

run "short_database_password" {
  command = plan
  override_data {
    target = data.azurerm_key_vault_secret.bootstrap["database-password"]
    values = { value = "short" }
  }
  expect_failures = [azurerm_postgresql_flexible_server.main]
}

run "short_proxy_token" {
  command = plan
  override_data {
    target = data.azurerm_key_vault_secret.bootstrap["proxy-token"]
    values = { value = "short" }
  }
  expect_failures = [azapi_resource.secret["proxy-token"]]
}
