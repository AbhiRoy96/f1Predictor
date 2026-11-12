mock_provider "azurerm" {
  mock_data "azurerm_resource_group" {
    defaults = {
      id       = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/poc"
      location = "eastus"
    }
  }
  mock_resource "azurerm_container_registry" {
    defaults = {
      login_server   = "poc.azurecr.io"
      admin_username = "poc"
      admin_password = "mock-registry-password"
    }
  }
  mock_resource "azurerm_container_app_environment" {
    defaults = {
      id             = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/poc/providers/Microsoft.App/managedEnvironments/gridcast-minpoc-env"
      default_domain = "test.eastus.azurecontainerapps.io"
    }
  }
  mock_resource "azurerm_storage_account" {
    defaults = {
      id                 = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/poc/providers/Microsoft.Storage/storageAccounts/stpoc"
      primary_access_key = "mock-storage-key"
    }
  }
}

mock_provider "azapi" {}

variables {
  resource_group_name     = "poc"
  release_tag             = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  initialized_release_tag = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  database_password       = "test password+with/@?characters"
  is_public_poc           = true
}

run "foundation_before_images" {
  command = plan
  plan_options {
    target = [azurerm_container_registry.main, azurerm_container_app_environment_storage.main]
  }
  variables {
    initialized_release_tag = null
  }
  assert {
    condition     = azurerm_container_registry.main.sku == "Basic" && azapi_resource.share.body.properties.enabledProtocols == "SMB"
    error_message = "The bootstrap must provision the registry and one SMB share before images or schema exist"
  }
}

run "init_before_applications" {
  command = plan
  plan_options {
    target = [azapi_resource.init]
  }
  variables {
    initialized_release_tag = null
  }
  assert {
    condition     = azapi_resource.init.body.properties.configuration.triggerType == "Manual"
    error_message = "Schema initialization must remain an explicit manual job"
  }
}

run "minimal_public_poc" {
  command = apply
  assert {
    condition     = azapi_resource.app["web"].body.properties.configuration.ingress.external && !azapi_resource.app["api"].body.properties.configuration.ingress.external && !azapi_resource.postgres.body.properties.configuration.ingress.external
    error_message = "Only web ingress may be external in the minimal PoC"
  }
  assert {
    condition     = !can(azapi_resource.app["worker"].body.properties.configuration.ingress) && azapi_resource.app["worker"].body.properties.template.scale.maxReplicas == 1
    error_message = "The worker must remain a singleton without ingress"
  }
  assert {
    condition     = azapi_resource.postgres.body.properties.template.volumes[0].mountOptions == "uid=70,gid=70,dir_mode=0700,file_mode=0600" && azapi_resource.postgres.body.properties.template.scale.maxReplicas == 1 && azapi_resource.share.name == "postgres-data"
    error_message = "PostgreSQL must use a single persistent share with alpine-compatible ownership"
  }
  assert {
    condition     = alltrue([for app in azapi_resource.app : !can(app.body.properties.template.volumes)])
    error_message = "Application artifacts must stay ephemeral; only PostgreSQL mounts the one share"
  }
  assert {
    condition     = alltrue([for app in azapi_resource.app : one([for env in app.body.properties.template.containers[0].env : env.value if env.name == "HOSTED_MODE"]) == "false"])
    error_message = "No-Entra PoC apps must not activate hosted authentication requirements"
  }
  assert {
    condition     = local.database_url == "postgresql+psycopg://gridcast:test%20password%2Bwith%2F%40%3Fcharacters@gridcast-minpoc-postgres:5432/gridcast?sslmode=disable"
    error_message = "The internal PostgreSQL connection must URI-encode the password"
  }
  assert {
    condition     = startswith(local.api_url, "https://gridcast-minpoc-api.internal.") && !strcontains(local.api_url, ":5000")
    error_message = "Web must address the API's HTTPS ingress, not its container port"
  }
  assert {
    condition     = azurerm_container_app_environment.main.infrastructure_subnet_id == null && azurerm_container_app_environment.main.log_analytics_workspace_id == null
    error_message = "The minimal environment must not depend on custom networking or a Log Analytics workspace"
  }
}

run "missing_schema_initialization" {
  command = plan
  variables {
    initialized_release_tag = null
  }
  expect_failures = [azapi_resource.app]
}

run "stale_schema_initialization" {
  command = plan
  variables {
    initialized_release_tag = "previous-release"
  }
  expect_failures = [azapi_resource.app]
}

run "missing_public_access_acknowledgement" {
  command = plan
  variables {
    is_public_poc = false
  }
  expect_failures = [azurerm_container_app_environment.main]
}

run "short_database_password" {
  command = plan
  variables {
    database_password = "short"
  }
  expect_failures = [azapi_resource.postgres]
}

run "invalid_revision_suffix" {
  command = plan
  variables {
    release_tag             = "Invalid_Tag"
    initialized_release_tag = "Invalid_Tag"
  }
  expect_failures = [azapi_resource.app]
}
