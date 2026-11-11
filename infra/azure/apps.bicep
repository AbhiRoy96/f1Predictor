metadata name = 'Gridcast applications'
metadata description = 'Deploy DB initialization first, then authenticated web, internal API and one serialized worker.'

@description('Resource prefix used by foundation.bicep.')
param prefix string
@description('Azure region of the existing managed environment.')
param location string = resourceGroup().location
@description('Immutable git SHA used as both image tag and revision suffix.')
@minLength(7)
@maxLength(40)
param releaseTag string
@description('Unique revision suffix. Use a fresh value for retries or rollback to an existing image tag.')
@maxLength(40)
param revisionSuffix string = releaseTag
@description('Entra application client ID, registered for this single-tenant web app.')
param entraClientId string
@description('Entra tenant ID used as the token issuer.')
param entraTenantId string = tenant().tenantId
@description('False deploys only the DB init job, leaving existing applications unchanged.')
param shouldDeployApps bool = true
@description('Web remains internal until the release script verifies its Entra configuration.')
param isWebExternal bool = false
@description('Controlled HTTP/protobuf OTLP endpoint. Null disables cloud telemetry.')
param otlpEndpoint string?

@description('Observed event times and UTC cron schedule for one enqueue-only forecast.')
type ScheduledForecast = {
  @description('Unique short job suffix.')
  name: string
  @description('Five-field UTC cron expression.')
  cron: string
  @description('Target season.')
  year: int
  @description('Exact target event name.')
  event: string
  @description('Publication phase.')
  phase: 'post_quali' | 'official_grid'
  @description('Observed qualifying completion, including timezone.')
  qualifyingCompletedAt: string
  @description('Target race start, including timezone.')
  raceStart: string
  @description('Observed official-grid publication; required for the official_grid phase.')
  gridPublishedAt: string?
}
@description('Optional per-event schedules. Empty disables automatic forecasting.')
param scheduledForecasts ScheduledForecast[] = []

var suffix = uniqueString(resourceGroup().id, prefix)
var kinds = ['web', 'api', 'worker']
resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' existing = { name: 'cr${suffix}' }
resource environment 'Microsoft.App/managedEnvironments@2026-07-01' existing = { name: '${prefix}-env' }
resource vault 'Microsoft.KeyVault/vaults@2025-05-01' existing = { name: 'kv-${suffix}' }
resource identities 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = [for kind in kinds: {
  name: '${prefix}-${kind}-identity'
}]
var databaseSecret = '${vault.properties.vaultUri}secrets/database-url'
var proxySecret = '${vault.properties.vaultUri}secrets/proxy-token'
var entraSecret = '${vault.properties.vaultUri}secrets/entra-client-secret'
var backendImage = '${registry.properties.loginServer}/gridcast-api:${releaseTag}'
var telemetry = [
  { name: 'OTEL_ENABLED', value: empty(otlpEndpoint) ? 'false' : 'true' }
  { name: 'OTEL_EXPORTER_OTLP_ENDPOINT', value: otlpEndpoint ?? '' }
  { name: 'OTEL_EXPORTER_OTLP_PROTOCOL', value: 'http/protobuf' }
  { name: 'OTEL_TRACES_SAMPLER', value: 'parentbased_traceidratio' }
  { name: 'OTEL_TRACES_SAMPLER_ARG', value: '0.1' }
  { name: 'PYROSCOPE_ENABLED', value: 'false' }
  { name: 'GPU_METRICS_ENABLED', value: 'false' }
  { name: 'HOSTED_MODE', value: 'true' }
  { name: 'DEPLOYMENT_ENVIRONMENT', value: 'azure' }
]

resource init 'Microsoft.App/jobs@2026-07-01' = {
  name: '${prefix}-db-init'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identities[2].id}': {} } }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 1
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: [{ server: registry.properties.loginServer, identity: identities[2].id }]
      secrets: [{ name: 'database-url', keyVaultUrl: databaseSecret, identity: identities[2].id }]
    }
    template: {
      containers: [{
        name: 'init'
        image: backendImage
        command: ['flask']
        args: ['--app', 'backend.app:create_app', 'db-init']
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: [
          { name: 'DATABASE_URL', secretRef: 'database-url' }
          { name: 'OTEL_ENABLED', value: 'false' }
        ]
      }]
    }
  }
}
resource api 'Microsoft.App/containerApps@2026-07-01' = if (shouldDeployApps) {
  name: '${prefix}-api'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identities[1].id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: false, targetPort: 5000, transport: 'http', allowInsecure: false }
      registries: [{ server: registry.properties.loginServer, identity: identities[1].id }]
      secrets: [
        { name: 'database-url', keyVaultUrl: databaseSecret, identity: identities[1].id }
        { name: 'proxy-token', keyVaultUrl: proxySecret, identity: identities[1].id }
      ]
    }
    template: {
      revisionSuffix: revisionSuffix
      scale: {
        minReplicas: 1
        maxReplicas: 2
        rules: [{ name: 'http', http: { metadata: { concurrentRequests: '30' } } }]
      }
      containers: [{
        name: 'api'
        image: backendImage
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: concat(telemetry, [
          { name: 'DATABASE_URL', secretRef: 'database-url' }
          { name: 'PROXY_AUTH_TOKEN', secretRef: 'proxy-token' }
          { name: 'OTEL_SERVICE_NAME', value: '${prefix}-api' }
        ])
        probes: [for kind in ['Startup', 'Readiness', 'Liveness']: {
          type: kind
          httpGet: { path: '/api/health', port: 5000 }
          periodSeconds: 10
          timeoutSeconds: 3
          failureThreshold: kind == 'Startup' ? 30 : 3
        }]
      }]
    }
  }
}
resource web 'Microsoft.App/containerApps@2026-07-01' = if (shouldDeployApps) {
  name: '${prefix}-web'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identities[0].id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: isWebExternal, targetPort: 3000, transport: 'http', allowInsecure: false }
      registries: [{ server: registry.properties.loginServer, identity: identities[0].id }]
      secrets: [
        { name: 'proxy-token', keyVaultUrl: proxySecret, identity: identities[0].id }
        { name: 'entra-client-secret', keyVaultUrl: entraSecret, identity: identities[0].id }
      ]
    }
    template: {
      revisionSuffix: revisionSuffix
      scale: {
        minReplicas: 1
        maxReplicas: 3
        rules: [{ name: 'http', http: { metadata: { concurrentRequests: '30' } } }]
      }
      containers: [{
        name: 'web'
        image: '${registry.properties.loginServer}/gridcast-web:${releaseTag}'
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: concat(telemetry, [
          { name: 'BACKEND_URL', value: 'https://${api!.properties.configuration.ingress.fqdn}' }
          { name: 'PROXY_AUTH_TOKEN', secretRef: 'proxy-token' }
          { name: 'OTEL_SERVICE_NAME', value: '${prefix}-web' }
        ])
        probes: [for kind in ['Startup', 'Readiness', 'Liveness']: {
          type: kind
          httpGet: { path: '/health', port: 3000 }
          periodSeconds: 10
          timeoutSeconds: 3
          failureThreshold: kind == 'Startup' ? 30 : 3
        }]
      }]
    }
  }
}
resource auth 'Microsoft.App/containerApps/authConfigs@2026-07-01' = if (shouldDeployApps) {
  parent: web
  name: 'current'
  properties: {
    platform: { enabled: true }
    globalValidation: {
      unauthenticatedClientAction: 'Return401'
      excludedPaths: ['/health']
    }
    httpSettings: { requireHttps: true }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: entraClientId
          clientSecretSettingName: 'entra-client-secret'
          openIdIssuer: '${az.environment().authentication.loginEndpoint}${entraTenantId}/v2.0'
        }
        validation: { allowedAudiences: [entraClientId, 'api://${entraClientId}'] }
      }
    }
    login: { tokenStore: { enabled: false } }
  }
}
resource worker 'Microsoft.App/containerApps@2026-07-01' = if (shouldDeployApps) {
  name: '${prefix}-worker'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identities[2].id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [{ server: registry.properties.loginServer, identity: identities[2].id }]
      secrets: [{ name: 'database-url', keyVaultUrl: databaseSecret, identity: identities[2].id }]
    }
    template: {
      revisionSuffix: revisionSuffix
      scale: { minReplicas: 1, maxReplicas: 1 }
      containers: [{
        name: 'worker'
        image: backendImage
        command: ['python', '-m', 'backend.worker']
        resources: { cpu: 2, memory: '4Gi' }
        env: concat(telemetry, [
          { name: 'DATABASE_URL', secretRef: 'database-url' }
          { name: 'OTEL_SERVICE_NAME', value: '${prefix}-worker' }
          { name: 'FASTF1_CACHE_DIR', value: '/tmp/fastf1' }
          { name: 'JOB_MAX_RUNTIME_SECONDS', value: '3600' }
          { name: 'JOB_HEARTBEAT_SECONDS', value: '15' }
        ])
        volumeMounts: [for name in ['data', 'models', 'reports']: {
          volumeName: name
          mountPath: '/app/${name}'
        }]
      }]
      volumes: [for name in ['data', 'models', 'reports']: {
        name: name
        storageType: 'AzureFile'
        storageName: name
        mountOptions: 'uid=10001,gid=10001,dir_mode=0770,file_mode=0660'
      }]
    }
  }
}
resource schedules 'Microsoft.App/jobs@2026-07-01' = [for forecast in scheduledForecasts: if (shouldDeployApps) {
  name: '${prefix}-${forecast.name}'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identities[0].id}': {} } }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 120
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: forecast.cron
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [{ server: registry.properties.loginServer, identity: identities[0].id }]
      secrets: [{ name: 'proxy-token', keyVaultUrl: proxySecret, identity: identities[0].id }]
    }
    template: {
      containers: [{
        name: 'enqueue'
        image: backendImage
        command: ['python', '-m', 'scripts.scheduled_forecast']
        args: concat([
          '--year', string(forecast.year), '--event', forecast.event, '--phase', forecast.phase
          '--qualifying-completed-at', forecast.qualifyingCompletedAt, '--race-start', forecast.raceStart
        ], forecast.phase == 'official_grid' ? ['--grid-published-at', forecast.gridPublishedAt!] : [])
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: [
          { name: 'BACKEND_URL', value: 'https://${api!.properties.configuration.ingress.fqdn}' }
          { name: 'PROXY_AUTH_TOKEN', secretRef: 'proxy-token' }
          { name: 'OTEL_ENABLED', value: 'false' }
        ]
      }]
    }
  }
}]

@description('Public URL only after the release script enables authenticated external ingress.')
output webUrl string? = shouldDeployApps ? 'https://${web!.properties.configuration.ingress.fqdn}' : null
@description('DB migration job name.')
output initJobName string = init.name
