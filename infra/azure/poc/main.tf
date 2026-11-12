data "azurerm_resource_group" "main" {
  name = var.resource_group_name
}

locals {
  suffix        = substr(sha256("${data.azurerm_resource_group.main.id}:${var.prefix}"), 0, 13)
  backend_image = "${azurerm_container_registry.main.login_server}/gridcast-api:${var.release_tag}"
  database_url  = "postgresql+psycopg://gridcast:${replace(urlencode(var.database_password), "+", "%20")}@${var.prefix}-postgres:5432/gridcast?sslmode=disable"
  api_url       = "https://${var.prefix}-api.internal.${azurerm_container_app_environment.main.default_domain}"
  registry = [{
    server            = azurerm_container_registry.main.login_server
    username          = azurerm_container_registry.main.admin_username
    passwordSecretRef = "registry-password"
  }]
  runtime_env = [
    { name = "HOSTED_MODE", value = "false" },
    { name = "OTEL_ENABLED", value = "false" },
    { name = "PYROSCOPE_ENABLED", value = "false" },
    { name = "GPU_METRICS_ENABLED", value = "false" },
    { name = "DEPLOYMENT_ENVIRONMENT", value = "azure-poc" }
  ]
  app_settings = {
    api    = { cpu = 0.5, memory = "1Gi", max_replicas = 2 }
    worker = { cpu = 1, memory = "2Gi", max_replicas = 1 }
    web    = { cpu = 0.5, memory = "1Gi", max_replicas = 2 }
  }
  ingress = {
    api = { external = false, targetPort = 5000, transport = "http", allowInsecure = false }
    web = { external = true, targetPort = 3000, transport = "http", allowInsecure = false }
  }
  container_extras = merge({
    worker                       = { command = ["python", "-m", "backend.worker"] }
    }, { for kind, port in { api = 5000, web = 3000 } : kind => {
      probes = [for probe in ["Startup", "Readiness", "Liveness"] : {
        type             = probe
        httpGet          = { path = kind == "api" ? "/api/health" : "/health", port = port }
        periodSeconds    = 10
        timeoutSeconds   = 3
        failureThreshold = probe == "Startup" ? 30 : 3
      }]
    }
  })
}

resource "azurerm_container_registry" "main" {
  name                = "cr${local.suffix}"
  resource_group_name = var.resource_group_name
  location            = data.azurerm_resource_group.main.location
  sku                 = "Basic"
  admin_enabled       = true
}

resource "azurerm_storage_account" "main" {
  name                            = "st${local.suffix}"
  resource_group_name             = var.resource_group_name
  location                        = data.azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  // Default ACA networking needs a reachable SMB endpoint and account-key auth.
  shared_access_key_enabled = true
  lifecycle {
    prevent_destroy = true
  }
}

resource "azapi_resource" "share" {
  type      = "Microsoft.Storage/storageAccounts/fileServices/shares@2025-06-01"
  name      = "postgres-data"
  parent_id = "${azurerm_storage_account.main.id}/fileServices/default"
  body = {
    properties = { shareQuota = 100, enabledProtocols = "SMB" }
  }
  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_container_app_environment" "main" {
  name                = "${var.prefix}-env"
  resource_group_name = var.resource_group_name
  location            = data.azurerm_resource_group.main.location
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
  lifecycle {
    precondition {
      condition     = var.is_public_poc
      error_message = "Set is_public_poc=true only for a disposable PoC with unauthenticated public access"
    }
  }
}

resource "azurerm_container_app_environment_storage" "main" {
  name                         = "postgres-storage"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.main.name
  share_name                   = azapi_resource.share.name
  access_key                   = azurerm_storage_account.main.primary_access_key
  access_mode                  = "ReadWrite"
}

resource "azapi_resource" "postgres" {
  type      = "Microsoft.App/containerApps@2025-07-01"
  name      = "${var.prefix}-postgres"
  parent_id = data.azurerm_resource_group.main.id
  location  = data.azurerm_resource_group.main.location
  body = {
    properties = {
      environmentId       = azurerm_container_app_environment.main.id
      workloadProfileName = "Consumption"
      configuration = {
        activeRevisionsMode = "Single"
        secrets             = [{ name = "db-password", value = var.database_password }]
        ingress             = { external = false, targetPort = 5432, exposedPort = 5432, transport = "tcp" }
      }
      template = {
        scale = { minReplicas = 1, maxReplicas = 1 }
        containers = [{
          name      = "postgres"
          image     = "postgres:17-alpine"
          resources = { cpu = 1, memory = "2Gi" }
          env = [
            { name = "POSTGRES_DB", value = "gridcast" },
            { name = "POSTGRES_USER", value = "gridcast" },
            { name = "POSTGRES_PASSWORD", secretRef = "db-password" },
            { name = "PGDATA", value = "/var/lib/postgresql/data/pgdata" }
          ]
          volumeMounts = [{ volumeName = "postgres-vol", mountPath = "/var/lib/postgresql/data" }]
          probes = [for probe in ["Startup", "Readiness", "Liveness"] : {
            type             = probe
            tcpSocket        = { port = 5432 }
            periodSeconds    = 10
            timeoutSeconds   = 3
            failureThreshold = probe == "Startup" ? 60 : 3
          }]
        }]
        volumes = [{
          name        = "postgres-vol"
          storageType = "AzureFile"
          storageName = azurerm_container_app_environment_storage.main.name
          // The alpine PostgreSQL image uses UID/GID 70, not Debian's 999.
          mountOptions = "uid=70,gid=70,dir_mode=0700,file_mode=0600"
        }]
      }
    }
  }
  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = length(var.database_password) >= 20
      error_message = "The PoC database password must contain at least twenty characters"
    }
  }
}

resource "azapi_resource" "init" {
  type      = "Microsoft.App/jobs@2025-07-01"
  name      = "${var.prefix}-db-init"
  parent_id = data.azurerm_resource_group.main.id
  location  = data.azurerm_resource_group.main.location
  body = {
    properties = {
      environmentId = azurerm_container_app_environment.main.id
      configuration = {
        triggerType         = "Manual"
        replicaTimeout      = 600
        replicaRetryLimit   = 1
        manualTriggerConfig = { parallelism = 1, replicaCompletionCount = 1 }
        registries          = local.registry
        secrets = [
          { name = "registry-password", value = azurerm_container_registry.main.admin_password },
          { name = "database-url", value = local.database_url }
        ]
      }
      template = {
        containers = [{
          name      = "init"
          image     = local.backend_image
          command   = ["flask"]
          args      = ["--app", "backend.app:create_app", "db-init"]
          resources = { cpu = 0.5, memory = "1Gi" }
          env       = concat(local.runtime_env, [{ name = "DATABASE_URL", secretRef = "database-url" }])
        }]
      }
    }
  }
  depends_on = [azapi_resource.postgres]
}

resource "azapi_resource" "app" {
  for_each  = local.app_settings
  type      = "Microsoft.App/containerApps@2025-07-01"
  name      = "${var.prefix}-${each.key}"
  parent_id = data.azurerm_resource_group.main.id
  location  = data.azurerm_resource_group.main.location
  body = {
    properties = {
      environmentId       = azurerm_container_app_environment.main.id
      workloadProfileName = "Consumption"
      configuration = merge({
        activeRevisionsMode = "Single"
        registries          = local.registry
        secrets = concat(
          [{ name = "registry-password", value = azurerm_container_registry.main.admin_password }],
          each.key == "web" ? [] : [{ name = "database-url", value = local.database_url }]
        )
      }, each.key == "worker" ? {} : { ingress = local.ingress[each.key] })
      template = {
        revisionSuffix = var.release_tag
        scale          = { minReplicas = 1, maxReplicas = each.value.max_replicas }
        containers = [merge({
          name      = each.key
          image     = each.key == "web" ? "${azurerm_container_registry.main.login_server}/gridcast-web:${var.release_tag}" : local.backend_image
          resources = { cpu = each.value.cpu, memory = each.value.memory }
          env = concat(local.runtime_env,
            each.key == "web" ? [{ name = "BACKEND_URL", value = local.api_url }] : [{ name = "DATABASE_URL", secretRef = "database-url" }],
            each.key == "worker" ? [
              { name = "FASTF1_CACHE_DIR", value = "/tmp/fastf1" },
              { name = "JOB_MAX_RUNTIME_SECONDS", value = "3600" },
              { name = "JOB_HEARTBEAT_SECONDS", value = "15" }
            ] : []
          )
        }, local.container_extras[each.key])]
      }
    }
  }
  depends_on = [azapi_resource.init]
  lifecycle {
    precondition {
      condition     = var.initialized_release_tag == var.release_tag
      error_message = "Run this release's db-init job and wait for Succeeded, then set initialized_release_tag=release_tag"
    }
    precondition {
      condition     = can(regex("^[a-z0-9][a-z0-9-]{0,39}$", var.release_tag)) && !strcontains(var.release_tag, "--") && !endswith(var.release_tag, "-")
      error_message = "Use an immutable release tag valid as an ACA revision suffix: up to forty lowercase letters, digits or single hyphens"
    }
  }
}
