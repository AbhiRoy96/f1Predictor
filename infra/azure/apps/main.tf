data "azurerm_resource_group" "main" {
  name = var.resource_group_name
}

data "azurerm_container_registry" "main" {
  name                = var.registry_name
  resource_group_name = var.resource_group_name
}

data "azurerm_container_app_environment" "main" {
  name                = "${var.prefix}-env"
  resource_group_name = var.resource_group_name
}

data "azurerm_user_assigned_identity" "main" {
  for_each            = toset(["api", "web", "worker"])
  name                = "${var.prefix}-${each.key}-identity"
  resource_group_name = var.resource_group_name
}

locals {
  backend_image = "${data.azurerm_container_registry.main.login_server}/gridcast-api:${var.release_tag}"
  vault_uri     = "https://${var.vault_name}.vault.azure.net"
  telemetry = [
    { name = "OTEL_ENABLED", value = var.otlp_endpoint == null || var.otlp_endpoint == "" ? "false" : "true" },
    { name = "OTEL_EXPORTER_OTLP_ENDPOINT", value = var.otlp_endpoint == null ? "" : var.otlp_endpoint },
    { name = "OTEL_EXPORTER_OTLP_PROTOCOL", value = "http/protobuf" },
    { name = "OTEL_TRACES_SAMPLER", value = "parentbased_traceidratio" },
    { name = "OTEL_TRACES_SAMPLER_ARG", value = "0.1" },
    { name = "PYROSCOPE_ENABLED", value = "false" },
    { name = "GPU_METRICS_ENABLED", value = "false" },
    { name = "HOSTED_MODE", value = "true" },
    { name = "DEPLOYMENT_ENVIRONMENT", value = "azure" }
  ]
  app_settings = {
    api = {
      secrets = ["database-url", "proxy-token"]
      env = [
        { name = "DATABASE_URL", secretRef = "database-url" },
        { name = "PROXY_AUTH_TOKEN", secretRef = "proxy-token" }
      ]
      port = 5000, health_path = "/api/health", max_replicas = 2
    }
    web = {
      secrets = ["proxy-token", "entra-client-secret"]
      env = [
        { name = "BACKEND_URL", value = "https://${var.prefix}-api.internal.${data.azurerm_container_app_environment.main.default_domain}" },
        { name = "PROXY_AUTH_TOKEN", secretRef = "proxy-token" }
      ]
      port = 3000, health_path = "/health", max_replicas = 3
    }
    worker = {
      secrets = ["database-url"]
      env = [
        { name = "DATABASE_URL", secretRef = "database-url" },
        { name = "FASTF1_CACHE_DIR", value = "/tmp/fastf1" },
        { name = "JOB_MAX_RUNTIME_SECONDS", value = "3600" },
        { name = "JOB_HEARTBEAT_SECONDS", value = "15" }
      ]
      port = 0, health_path = "", max_replicas = 1
    }
  }
  container_extras = merge({
    worker = {
      command = ["python", "-m", "backend.worker"]
      volumeMounts = [for name in ["data", "models", "reports"] : {
        volumeName = name, mountPath = "/app/${name}"
      }]
    }
    }, { for kind in ["api", "web"] : kind => {
      probes = [for probe in ["Startup", "Readiness", "Liveness"] : {
        type             = probe
        httpGet          = { path = local.app_settings[kind].health_path, port = local.app_settings[kind].port }
        periodSeconds    = 10
        timeoutSeconds   = 3
        failureThreshold = probe == "Startup" ? 30 : 3
      }]
    }
  })
}

resource "azapi_resource" "init" {
  type      = "Microsoft.App/jobs@2025-07-01"
  name      = "${var.prefix}-db-init"
  parent_id = data.azurerm_resource_group.main.id
  location  = data.azurerm_resource_group.main.location
  identity {
    type         = "UserAssigned"
    identity_ids = [data.azurerm_user_assigned_identity.main["worker"].id]
  }
  body = {
    properties = {
      environmentId = data.azurerm_container_app_environment.main.id
      configuration = {
        triggerType         = "Manual"
        replicaTimeout      = 600
        replicaRetryLimit   = 1
        manualTriggerConfig = { parallelism = 1, replicaCompletionCount = 1 }
        registries = [{
          server   = data.azurerm_container_registry.main.login_server
          identity = data.azurerm_user_assigned_identity.main["worker"].id
        }]
        secrets = [{
          name        = "database-url"
          keyVaultUrl = "${local.vault_uri}/secrets/database-url"
          identity    = data.azurerm_user_assigned_identity.main["worker"].id
        }]
      }
      template = {
        containers = [{
          name      = "init"
          image     = local.backend_image
          command   = ["flask"]
          args      = ["--app", "backend.app:create_app", "db-init"]
          resources = { cpu = 0.25, memory = "0.5Gi" }
          env = [
            { name = "DATABASE_URL", secretRef = "database-url" },
            { name = "OTEL_ENABLED", value = "false" }
          ]
        }]
      }
    }
  }
}

resource "azapi_resource" "app" {
  for_each  = local.app_settings
  type      = "Microsoft.App/containerApps@2025-07-01"
  name      = "${var.prefix}-${each.key}"
  parent_id = data.azurerm_resource_group.main.id
  location  = data.azurerm_resource_group.main.location
  identity {
    type         = "UserAssigned"
    identity_ids = [data.azurerm_user_assigned_identity.main[each.key].id]
  }
  body = {
    properties = {
      environmentId       = data.azurerm_container_app_environment.main.id
      workloadProfileName = "Consumption"
      configuration = merge({
        activeRevisionsMode = "Single"
        registries = [{
          server   = data.azurerm_container_registry.main.login_server
          identity = data.azurerm_user_assigned_identity.main[each.key].id
        }]
        secrets = [for name in each.value.secrets : {
          name        = name
          keyVaultUrl = "${local.vault_uri}/secrets/${name}"
          identity    = data.azurerm_user_assigned_identity.main[each.key].id
        }]
        }, each.key == "worker" ? {} : {
        ingress = { external = false, targetPort = each.value.port, transport = "http", allowInsecure = false }
      })
      template = merge({
        revisionSuffix = var.revision_suffix
        scale = merge({
          minReplicas = 1
          maxReplicas = each.value.max_replicas
          }, each.key == "worker" ? {} : {
          rules = [{ name = "http", http = { metadata = { concurrentRequests = "30" } } }]
        })
        containers = [merge({
          name      = each.key
          image     = each.key == "web" ? "${data.azurerm_container_registry.main.login_server}/gridcast-web:${var.release_tag}" : local.backend_image
          resources = { cpu = each.key == "worker" ? 2 : 0.5, memory = each.key == "worker" ? "4Gi" : "1Gi" }
          env       = concat(local.telemetry, each.value.env, [{ name = "OTEL_SERVICE_NAME", value = "${var.prefix}-${each.key}" }])
        }, local.container_extras[each.key])]
        }, each.key == "worker" ? {
        volumes = [for name in ["data", "models", "reports"] : {
          name         = name
          storageType  = "AzureFile"
          storageName  = name
          mountOptions = "uid=10001,gid=10001,dir_mode=0770,file_mode=0660"
        }]
      } : {})
    }
  }
}

resource "azapi_resource" "auth" {
  type      = "Microsoft.App/containerApps/authConfigs@2025-07-01"
  name      = "current"
  parent_id = azapi_resource.app["web"].id
  body = {
    properties = {
      platform         = { enabled = true }
      globalValidation = { unauthenticatedClientAction = "Return401", excludedPaths = ["/health"] }
      httpSettings     = { requireHttps = true }
      identityProviders = {
        azureActiveDirectory = {
          enabled = true
          registration = {
            clientId                = var.entra_client_id
            clientSecretSettingName = "entra-client-secret"
            openIdIssuer            = "https://login.microsoftonline.com/${var.entra_tenant_id}/v2.0"
          }
          validation = { allowedAudiences = [var.entra_client_id, "api://${var.entra_client_id}"] }
        }
      }
      login = { tokenStore = { enabled = false } }
    }
  }
}

resource "azapi_resource" "schedule" {
  for_each  = { for forecast in var.scheduled_forecasts : forecast.name => forecast }
  type      = "Microsoft.App/jobs@2025-07-01"
  name      = "${var.prefix}-${each.key}"
  parent_id = data.azurerm_resource_group.main.id
  location  = data.azurerm_resource_group.main.location
  identity {
    type         = "UserAssigned"
    identity_ids = [data.azurerm_user_assigned_identity.main["web"].id]
  }
  body = {
    properties = {
      environmentId = data.azurerm_container_app_environment.main.id
      configuration = {
        triggerType           = "Schedule"
        replicaTimeout        = 120
        replicaRetryLimit     = 0
        scheduleTriggerConfig = { cronExpression = each.value.cron, parallelism = 1, replicaCompletionCount = 1 }
        registries = [{
          server   = data.azurerm_container_registry.main.login_server
          identity = data.azurerm_user_assigned_identity.main["web"].id
        }]
        secrets = [{
          name        = "proxy-token"
          keyVaultUrl = "${local.vault_uri}/secrets/proxy-token"
          identity    = data.azurerm_user_assigned_identity.main["web"].id
        }]
      }
      template = {
        containers = [{
          name    = "enqueue"
          image   = local.backend_image
          command = ["python", "-m", "scripts.scheduled_forecast"]
          args = concat([
            "--year", tostring(each.value.year), "--event", each.value.event, "--phase", each.value.phase,
            "--qualifying-completed-at", each.value.qualifying_completed_at, "--race-start", each.value.race_start
          ], each.value.phase == "official_grid" ? ["--grid-published-at", each.value.grid_published_at] : [])
          resources = { cpu = 0.25, memory = "0.5Gi" }
          env = [
            { name = "BACKEND_URL", value = "https://${var.prefix}-api.internal.${data.azurerm_container_app_environment.main.default_domain}" },
            { name = "PROXY_AUTH_TOKEN", secretRef = "proxy-token" },
            { name = "OTEL_ENABLED", value = "false" }
          ]
        }]
      }
    }
  }
  depends_on = [azapi_resource.app]
  lifecycle {
    precondition {
      condition     = contains(["post_quali", "official_grid"], each.value.phase) && (each.value.phase != "official_grid" || each.value.grid_published_at != null)
      error_message = "Schedules require a supported phase and an observed grid publication timestamp for official_grid"
    }
  }
}
