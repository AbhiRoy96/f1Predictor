metadata name = 'Gridcast foundation'
metadata description = 'Private PostgreSQL, ACA networking, worker storage, registry, secrets and cost controls.'

@description('Short lowercase resource prefix, unique within the resource group.')
@minLength(3)
@maxLength(12)
param prefix string

@description('Azure region supporting ACA Consumption and PostgreSQL Flexible Server.')
param location string = resourceGroup().location

@description('PostgreSQL administrator password. Supply from a secure deployment parameter reference.')
@secure()
@minLength(20)
param databasePassword string

@description('Shared private API proxy credential. Supply from a secure deployment parameter reference.')
@secure()
@minLength(32)
param proxyToken string

@description('Client secret for the operator-created single-tenant Entra web application.')
@secure()
param entraClientSecret string

@description('Budget notification recipients. Supply operator addresses at deployment time.')
@minLength(1)
param budgetContactEmails string[]

@description('Monthly resource-group budget in the subscription billing currency.')
@minValue(1)
param monthlyBudget int = 100

@description('First day of the budget period, in ISO UTC format. Must be in the current month.')
param budgetStartDate string

@description('Log Analytics daily ingestion cap in GiB.')
@minValue(1)
param dailyLogCap int = 1

@description('Log retention in days.')
@minValue(30)
param logRetentionDays int = 30

@description('PostgreSQL backup retention in days.')
@minValue(7)
@maxValue(35)
param databaseBackupDays int = 14

var suffix = uniqueString(resourceGroup().id, prefix)
var names = {
  registry: 'cr${suffix}'
  storage: 'st${suffix}'
  vault: 'kv-${suffix}'
  postgres: 'pg-${suffix}'
  environment: '${prefix}-env'
}
var shareNames = ['data', 'models', 'reports']
var identityKinds = ['web', 'api', 'worker']

resource network 'Microsoft.Network/virtualNetworks@2025-05-01' = {
  name: '${prefix}-vnet'
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.42.0.0/16'] }
    subnets: [
      {
        name: 'apps'
        properties: {
          addressPrefix: '10.42.0.0/23'
          delegations: [
            { name: 'aca', properties: { serviceName: 'Microsoft.App/environments' } }
          ]
          serviceEndpoints: [{ service: 'Microsoft.Storage' }]
        }
      }
      {
        name: 'postgres'
        properties: {
          addressPrefix: '10.42.2.0/27'
          delegations: [
            { name: 'postgres', properties: { serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers' } }
          ]
        }
      }
    ]
  }
}

resource postgresDns 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: '${prefix}.postgres.database.azure.com'
  location: 'global'
}
resource dnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: postgresDns
  name: '${prefix}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: network.id }
  }
}
resource databaseServer 'Microsoft.DBforPostgreSQL/flexibleServers@2026-10-01' = {
  name: names.postgres
  location: location
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '17'
    administratorLogin: 'gridcast'
    administratorLoginPassword: databasePassword
    authConfig: { passwordAuth: 'Enabled' }
    storage: { storageSizeGB: 32, autoGrow: 'Enabled' }
    backup: { backupRetentionDays: databaseBackupDays, geoRedundantBackup: 'Disabled' }
    network: {
      delegatedSubnetResourceId: network.properties.subnets[1].id
      privateDnsZoneArmResourceId: postgresDns.id
      publicNetworkAccess: 'Disabled'
    }
  }
  dependsOn: [dnsLink]
}
resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2026-10-01' = {
  parent: databaseServer
  name: 'gridcast'
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}
resource databaseTls 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2026-10-01' = {
  parent: databaseServer
  name: 'require_secure_transport'
  properties: { value: 'on', source: 'user-override' }
}
resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name: names.registry
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false, anonymousPullEnabled: false }
}
resource identities 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = [for kind in identityKinds: {
  name: '${prefix}-${kind}-identity'
  location: location
}]
resource pullRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (kind, index) in identityKinds: {
  name: guid(registry.id, identities[index].id, 'AcrPull')
  scope: registry
  properties: {
    principalId: identities[index].properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}]
resource vault 'Microsoft.KeyVault/vaults@2025-05-01' = {
  name: names.vault
  location: location
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enablePurgeProtection: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Enabled'
  }
}
var secretValues = [
  {
    name: 'database-url'
    value: 'postgresql+psycopg://gridcast:${uriComponent(databasePassword)}@${databaseServer.properties.fullyQualifiedDomainName}:5432/gridcast?sslmode=require'
  }
  { name: 'proxy-token', value: proxyToken }
  { name: 'entra-client-secret', value: entraClientSecret }
]
resource secrets 'Microsoft.KeyVault/vaults/secrets@2025-05-01' = [for (name, index) in ['database-url', 'proxy-token', 'entra-client-secret']: {
  parent: vault
  name: name
  properties: { value: secretValues[index].value }
}]
var secretAccess = [
  { secret: 0, identity: 1 }
  { secret: 0, identity: 2 }
  { secret: 1, identity: 0 }
  { secret: 1, identity: 1 }
  { secret: 2, identity: 0 }
]
resource secretRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for access in secretAccess: {
  name: guid(secrets[access.secret].id, identities[access.identity].id, 'SecretsUser')
  scope: secrets[access.secret]
  properties: {
    principalId: identities[access.identity].properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}]
resource storage 'Microsoft.Storage/storageAccounts@2025-06-01' = {
  name: names.storage
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    // ACA AzureFile SMB mounts require an account key; access is restricted to the app subnet.
    allowSharedKeyAccess: true
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
      virtualNetworkRules: [{ id: network.properties.subnets[0].id, action: 'Allow' }]
    }
  }
}
resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2025-06-01' = {
  parent: storage
  name: 'default'
  properties: { shareDeleteRetentionPolicy: { enabled: true, days: 14 } }
}
resource shares 'Microsoft.Storage/storageAccounts/fileServices/shares@2025-06-01' = [for name in shareNames: {
  parent: fileService
  name: name
  properties: { shareQuota: 100, enabledProtocols: 'SMB' }
}]
resource logs 'Microsoft.OperationalInsights/workspaces@2025-07-01' = {
  name: '${prefix}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: logRetentionDays
    workspaceCapping: { dailyQuotaGb: dailyLogCap }
  }
}
resource environment 'Microsoft.App/managedEnvironments@2026-07-01' = {
  name: names.environment
  location: location
  properties: {
    vnetConfiguration: { infrastructureSubnetId: network.properties.subnets[0].id }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}
resource mounts 'Microsoft.App/managedEnvironments/storages@2026-07-01' = [for (name, index) in shareNames: {
  parent: environment
  name: name
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: shares[index].name
      accessMode: 'ReadWrite'
    }
  }
}]
resource budget 'Microsoft.Consumption/budgets@2024-08-01' = {
  name: '${prefix}-monthly'
  properties: {
    category: 'Cost'
    amount: monthlyBudget
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStartDate }
    notifications: {
      actual80: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: budgetContactEmails
      }
      forecast100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: budgetContactEmails
      }
    }
  }
}

@description('Registry name for remote linux/amd64 builds.')
output registryName string = registry.name
@description('Managed environment name.')
output environmentName string = environment.name
@description('Dedicated Key Vault name. Secret values are never output.')
output vaultName string = vault.name
@description('Storage account name for snapshot operations.')
output storageName string = storage.name
@description('Private PostgreSQL server name for restore drills.')
output postgresName string = databaseServer.name
@description('Register this redirect URI on the Entra app before enabling external web ingress.')
output entraRedirectUri string = 'https://${prefix}-web.${environment.properties.defaultDomain}/.auth/login/aad/callback'
