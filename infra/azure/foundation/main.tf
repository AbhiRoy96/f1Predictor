data "azurerm_resource_group" "main" {
  name = var.resource_group_name
}

data "azurerm_client_config" "main" {}

data "azurerm_key_vault_secret" "bootstrap" {
  for_each     = toset(["database-password", "proxy-token", "entra-client-secret"])
  name         = "gridcast-${each.key}"
  key_vault_id = var.bootstrap_key_vault_id
}

locals {
  suffix                = substr(sha256("${data.azurerm_resource_group.main.id}:${var.prefix}"), 0, 13)
  location              = data.azurerm_resource_group.main.location
  shares                = toset(["data", "models", "reports"])
  database_password_uri = replace(urlencode(data.azurerm_key_vault_secret.bootstrap["database-password"].value), "+", "%20")
  secret_access = {
    api_database    = { secret = "database-url", identity = "api" }
    worker_database = { secret = "database-url", identity = "worker" }
    web_proxy       = { secret = "proxy-token", identity = "web" }
    api_proxy       = { secret = "proxy-token", identity = "api" }
    web_entra       = { secret = "entra-client-secret", identity = "web" }
  }
  secret_values = {
    database-url        = "postgresql+psycopg://gridcast:${local.database_password_uri}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/gridcast?sslmode=require"
    proxy-token         = data.azurerm_key_vault_secret.bootstrap["proxy-token"].value
    entra-client-secret = data.azurerm_key_vault_secret.bootstrap["entra-client-secret"].value
  }
}

resource "azurerm_virtual_network" "main" {
  name                = "${var.prefix}-vnet"
  resource_group_name = var.resource_group_name
  location            = local.location
  address_space       = ["10.42.0.0/16"]
}

resource "azurerm_subnet" "apps" {
  name                 = "apps"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.42.0.0/23"]
  service_endpoints    = ["Microsoft.Storage"]
  delegation {
    name = "aca"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "postgres" {
  name                 = "postgres"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.42.2.0/27"]
  delegation {
    name = "postgres"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_private_dns_zone" "main" {
  name                = "${var.prefix}.postgres.database.azure.com"
  resource_group_name = var.resource_group_name
}

resource "azurerm_private_dns_zone_virtual_network_link" "main" {
  name                  = "${var.prefix}-link"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.main.name
  virtual_network_id    = azurerm_virtual_network.main.id
  registration_enabled  = false
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                          = "pg-${local.suffix}"
  resource_group_name           = var.resource_group_name
  location                      = local.location
  version                       = "17"
  administrator_login           = "gridcast"
  administrator_password        = data.azurerm_key_vault_secret.bootstrap["database-password"].value
  delegated_subnet_id           = azurerm_subnet.postgres.id
  private_dns_zone_id           = azurerm_private_dns_zone.main.id
  public_network_access_enabled = false
  sku_name                      = "B_Standard_B1ms"
  storage_mb                    = 32768
  auto_grow_enabled             = true
  backup_retention_days         = var.database_backup_days
  geo_redundant_backup_enabled  = false
  authentication {
    password_auth_enabled = true
  }
  depends_on = [azurerm_private_dns_zone_virtual_network_link.main]
  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = length(data.azurerm_key_vault_secret.bootstrap["database-password"].value) >= 20
      error_message = "The bootstrap database password must contain at least twenty characters"
    }
  }
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = "gridcast"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_postgresql_flexible_server_configuration" "tls" {
  name      = "require_secure_transport"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "on"
}

resource "azurerm_container_registry" "main" {
  name                = "cr${local.suffix}"
  resource_group_name = var.resource_group_name
  location            = local.location
  sku                 = "Basic"
  admin_enabled       = false
}

resource "azurerm_user_assigned_identity" "main" {
  for_each            = toset(["web", "api", "worker"])
  name                = "${var.prefix}-${each.key}-identity"
  resource_group_name = var.resource_group_name
  location            = local.location
}

resource "azurerm_role_assignment" "pull" {
  for_each                         = azurerm_user_assigned_identity.main
  scope                            = azurerm_container_registry.main.id
  role_definition_name             = "AcrPull"
  principal_id                     = each.value.principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_key_vault" "main" {
  name                       = "kv-${local.suffix}"
  resource_group_name        = var.resource_group_name
  location                   = local.location
  tenant_id                  = data.azurerm_client_config.main.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = true
  soft_delete_retention_days = 90
}

// ARM secret writes avoid granting the foundation principal vault data-plane access.
resource "azapi_resource" "secret" {
  for_each  = toset(["database-url", "proxy-token", "entra-client-secret"])
  type      = "Microsoft.KeyVault/vaults/secrets@2025-05-01"
  name      = each.key
  parent_id = azurerm_key_vault.main.id
  body = {
    properties = { value = local.secret_values[each.key] }
  }
  lifecycle {
    precondition {
      condition     = each.key != "proxy-token" || length(data.azurerm_key_vault_secret.bootstrap["proxy-token"].value) >= 32
      error_message = "The bootstrap proxy token must contain at least thirty-two characters"
    }
  }
}

resource "azurerm_role_assignment" "secret" {
  for_each                         = local.secret_access
  scope                            = azapi_resource.secret[each.value.secret].id
  role_definition_name             = "Key Vault Secrets User"
  principal_id                     = azurerm_user_assigned_identity.main[each.value.identity].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_storage_account" "main" {
  name                            = "st${local.suffix}"
  resource_group_name             = var.resource_group_name
  location                        = local.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  // ACA SMB mounts require an account key on this dedicated account.
  shared_access_key_enabled = true
  network_rules {
    default_action             = "Deny"
    bypass                     = ["AzureServices"]
    virtual_network_subnet_ids = [azurerm_subnet.apps.id]
  }
  share_properties {
    retention_policy {
      days = 14
    }
  }
  lifecycle {
    prevent_destroy = true
  }
}

// Manage shares through ARM rather than opening the storage firewall to Terraform.
resource "azapi_resource" "share" {
  for_each  = local.shares
  type      = "Microsoft.Storage/storageAccounts/fileServices/shares@2025-06-01"
  name      = each.key
  parent_id = "${azurerm_storage_account.main.id}/fileServices/default"
  body = {
    properties = { shareQuota = 100, enabledProtocols = "SMB" }
  }
  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "${var.prefix}-logs"
  resource_group_name = var.resource_group_name
  location            = local.location
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  daily_quota_gb      = var.daily_log_cap
}

resource "azurerm_container_app_environment" "main" {
  name                       = "${var.prefix}-env"
  resource_group_name        = var.resource_group_name
  location                   = local.location
  infrastructure_subnet_id   = azurerm_subnet.apps.id
  logs_destination           = "log-analytics"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

resource "azurerm_container_app_environment_storage" "main" {
  for_each                     = local.shares
  name                         = each.key
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.main.name
  share_name                   = azapi_resource.share[each.key].name
  access_key                   = azurerm_storage_account.main.primary_access_key
  access_mode                  = "ReadWrite"
}

resource "azurerm_consumption_budget_resource_group" "main" {
  name              = "${var.prefix}-monthly"
  resource_group_id = data.azurerm_resource_group.main.id
  amount            = var.monthly_budget
  time_grain        = "Monthly"
  time_period {
    start_date = var.budget_start_date
  }
  notification {
    enabled        = true
    operator       = "GreaterThanOrEqualTo"
    threshold      = 80
    threshold_type = "Actual"
    contact_emails = var.budget_contact_emails
  }
  notification {
    enabled        = true
    operator       = "GreaterThanOrEqualTo"
    threshold      = 100
    threshold_type = "Forecasted"
    contact_emails = var.budget_contact_emails
  }
}
