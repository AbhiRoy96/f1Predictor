mock_provider "azurerm" {
  mock_data "azurerm_resource_group" {
    defaults = {
      id       = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/staging"
      location = "eastus"
    }
  }
  mock_data "azurerm_container_registry" {
    defaults = { login_server = "test.azurecr.io" }
  }
  mock_data "azurerm_container_app_environment" {
    defaults = {
      id             = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/staging/providers/Microsoft.App/managedEnvironments/gridcast-env"
      default_domain = "test.eastus.azurecontainerapps.io"
    }
  }
  mock_data "azurerm_user_assigned_identity" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/staging/providers/Microsoft.ManagedIdentity/userAssignedIdentities/test"
    }
  }
}

mock_provider "azapi" {}

variables {
  resource_group_name  = "staging"
  registry_name        = "crtest"
  vault_name           = "test"
  storage_account_name = "sttest"
  release_tag          = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  revision_suffix      = "aaaaaaa-20261111000000"
  entra_client_id      = "00000000-0000-0000-0000-000000000001"
  entra_tenant_id      = "00000000-0000-0000-0000-000000000002"
}

run "release_plan" {
  command = plan
  variables {
    scheduled_forecasts = [{
      name                    = "test-forecast"
      cron                    = "0 1 1 1 *"
      year                    = 2026
      event                   = "Test Grand Prix"
      phase                   = "official_grid"
      qualifying_completed_at = "2026-01-01T10:00:00Z"
      race_start              = "2026-01-02T10:00:00Z"
      grid_published_at       = "2026-01-01T11:00:00Z"
    }]
  }
  assert {
    condition     = !azapi_resource.app["web"].body.properties.configuration.ingress.external && !azapi_resource.app["api"].body.properties.configuration.ingress.external
    error_message = "A Terraform release must never expose unauthenticated ingress"
  }
  assert {
    condition     = !can(azapi_resource.app["worker"].body.properties.configuration.ingress) && !can(azapi_resource.schedule["test-forecast"].body.properties.template.volumes)
    error_message = "Only the worker may mount artifacts; it must not have ingress"
  }
  assert {
    condition     = contains(azapi_resource.schedule["test-forecast"].body.properties.template.containers[0].args, "2026-01-01T11:00:00Z")
    error_message = "Official-grid schedules must forward the observed publication time"
  }
}

run "missing_grid_publication" {
  command = plan
  variables {
    scheduled_forecasts = [{
      name                    = "invalid"
      cron                    = "0 1 1 1 *"
      year                    = 2026
      event                   = "Test Grand Prix"
      phase                   = "official_grid"
      qualifying_completed_at = "2026-01-01T10:00:00Z"
      race_start              = "2026-01-02T10:00:00Z"
    }]
  }
  expect_failures = [azapi_resource.schedule["invalid"]]
}

run "post_qualifying_without_grid" {
  command = plan
  variables {
    scheduled_forecasts = [{
      name                    = "post-quali"
      cron                    = "0 1 1 1 *"
      year                    = 2026
      event                   = "Test Grand Prix"
      phase                   = "post_quali"
      qualifying_completed_at = "2026-01-01T10:00:00Z"
      race_start              = "2026-01-02T10:00:00Z"
    }]
  }
  assert {
    condition     = !contains(azapi_resource.schedule["post-quali"].body.properties.template.containers[0].args, "--grid-published-at")
    error_message = "Post-qualifying forecasts must not invent a grid publication timestamp"
  }
}
