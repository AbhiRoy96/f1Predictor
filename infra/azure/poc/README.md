---
title: Bare minimum Azure Container Apps PoC
description: Isolated Terraform for ACA, Basic ACR and one PostgreSQL Azure Files share.
---

## Scope and limitations

Implements [section 4 of the PoC architecture](../../../docs/POC_README.md#4-bare-minimum-poc-aca-acr--storage-account-only)
without changing the existing [staging infrastructure](../README.md).
Use a **new, dedicated resource group and state**, never the staging state.

For an automated PowerShell deployment, use the separate
[PoC running guide](../../../docs/RUN_MINIMAL_POC.md) and
[Deploy-MinimalPoc.ps1](../../../scripts/Deploy-MinimalPoc.ps1).
The manual commands below remain available for individual steps.

The only provisioned service families are:

- Basic ACR, with admin credentials stored in native ACA secrets for image pulls
- Standard LRS StorageV2, with one SMB share named `postgres-data`
- A default-network ACA Consumption environment, four container apps and a manual job

There is no Entra/EasyAuth, Key Vault, managed identity, managed PostgreSQL, custom
VNet/subnet/DNS, Log Analytics workspace, budget, scheduler or backup automation.
The existing staging release and backup workflows are not used by this PoC.

Web is publicly reachable without sign-in. API HTTP ingress and PostgreSQL TCP
ingress are internal; worker has no ingress. `HOSTED_MODE=false` intentionally
disables the Entra/proxy checks in the existing application. Anyone who reaches
web can access the PoC and enqueue work. Use only disposable, non-sensitive data.
`is_public_poc=true` is required as explicit acknowledgement.

Only PostgreSQL persists data. Worker cache, models, reports and downloaded data
are ephemeral and disappear on replacement. This setup cannot provide durable
forecast artifacts across worker restarts. PostgreSQL has one replica, no HA,
no managed backups and no point-in-time restore. Internal database transport is
unencrypted (`sslmode=disable`); HTTPS is required for web/API ingress.

The storage endpoint is reachable on the public network because there is no
custom VNet; file access still requires its account key and anonymous blob access
is disabled. Database passwords, registry credentials and storage keys are stored
in Terraform state. Sensitive marking redacts normal output, not the state or saved
plans. The explicit local backend avoids another storage service. Keep the local
state encrypted, backed up securely and out of Git; never share its contents.

PostgreSQL uses `postgres:17-alpine`, whose postgres account is UID/GID **70**, not
999. The SMB mount presents directory/file modes `0700`/`0600`. Azure Files SMB
database durability, locking, initialization and recovery must be tested in Azure;
mocked Terraform tests do not establish database suitability. Do not treat this as
a production-supported database storage design. If tenant policy disallows public
storage or ACR admin credentials, this exact minimal architecture cannot be used.

## Inputs and outputs

See [variables.tf](variables.tf) for all inputs:

- Required: `resource_group_name`, `database_password`, `release_tag`
- Optional: `prefix` (default `gridcast-minpoc`)
- Explicit opt-in: `is_public_poc` (default false)
- Release gate: `initialized_release_tag` (default null)

The release tag must be valid as an ACA revision suffix; use a full lowercase
source SHA. [outputs.tf](outputs.tf) exposes resource names, the registry hostname,
the migration job name and the public web URL, never secret values.

## Prepare and provision the foundation

Use PowerShell 7 from the repository root, Terraform 1.7+, Azure CLI with the
Container Apps extension, and Docker for local image pushes. Have a subscription
administrator register `Microsoft.App`, `Microsoft.ContainerRegistry` and
`Microsoft.Storage`. Supply an approved Python package index for image builds
where public PyPI is blocked.

Copy [terraform.tfvars.example](terraform.tfvars.example) to the ignored local file
below. Set a distinct resource group and prefix. Leave secret values out of it.

```powershell
Copy-Item infra\azure\poc\terraform.tfvars.example infra\azure\poc\poc.local.tfvars
az login
if ($LASTEXITCODE) { throw 'Azure login failed.' }
$env:ARM_SUBSCRIPTION_ID = '<poc-subscription-id>'
az account set --subscription $env:ARM_SUBSCRIPTION_ID
if ($LASTEXITCODE) { throw 'Subscription selection failed.' }
$rg = 'gridcast-minpoc-rg'
az group create --name $rg --location eastus
if ($LASTEXITCODE) { throw 'Resource group creation failed.' }
$env:TF_VAR_release_tag = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE) { throw 'Could not resolve source SHA.' }
$password = Read-Host 'PoC database password (at least 20 characters)' -AsSecureString
$env:TF_VAR_database_password = [System.Net.NetworkCredential]::new('', $password).Password
Remove-Item Env:TF_VAR_initialized_release_tag -ErrorAction SilentlyContinue

function Invoke-PocTerraform {
    & terraform '-chdir=infra\azure\poc' @args
    if ($LASTEXITCODE) { throw 'PoC Terraform command failed; do not continue.' }
}

Invoke-PocTerraform init
Invoke-PocTerraform validate
Invoke-PocTerraform plan -var-file=poc.local.tfvars -target=azurerm_container_registry.main -target=azurerm_container_app_environment_storage.main -out=foundation.tfplan
# Review the plan before applying.
Invoke-PocTerraform apply foundation.tfplan
Remove-Item infra\azure\poc\foundation.tfplan
$acr = Invoke-PocTerraform output -raw registry_name
$server = Invoke-PocTerraform output -raw registry_login_server
```

The targeted foundation plan includes the registry, environment, storage account,
share and mount registration, but no apps. Targeting is intentional only for this
initial bootstrap; subsequent deployment must finish with a full plan.
Do not run a full apply before building images and initializing the schema.

## Publish existing local images

The backend image is shared by API, worker and db-init; only two repositories
need pushing. Confirm your local images contain the intended source and are
`linux/amd64` before tagging with the SHA. If needed, rebuild first with
`docker compose build`; its package index comes from your ignored root `.env`.

```powershell
az acr login --name $acr
if ($LASTEXITCODE) { throw 'Registry login failed.' }
docker tag gridcast-api:latest "${server}/gridcast-api:$env:TF_VAR_release_tag"
if ($LASTEXITCODE) { throw 'Backend image tagging failed.' }
docker tag gridcast-web:latest "${server}/gridcast-web:$env:TF_VAR_release_tag"
if ($LASTEXITCODE) { throw 'Web image tagging failed.' }
docker push "${server}/gridcast-api:$env:TF_VAR_release_tag"
if ($LASTEXITCODE) { throw 'Backend image push failed.' }
docker push "${server}/gridcast-web:$env:TF_VAR_release_tag"
if ($LASTEXITCODE) { throw 'Web image push failed.' }
```

Alternatively, build remotely with `az acr build --platform linux/amd64` using the
same image names and tag. Remote builders need access to the chosen package index.

## Deploy PostgreSQL and initialize the schema

```powershell
Invoke-PocTerraform plan -var-file=poc.local.tfvars -target=azapi_resource.init -out=init.tfplan
# Review: only PostgreSQL, db-init and their dependencies should be added.
Invoke-PocTerraform apply init.tfplan
Remove-Item infra\azure\poc\init.tfplan
$job = Invoke-PocTerraform output -raw init_job_name
$execution = az containerapp job start --resource-group $rg --name $job --output json
if ($LASTEXITCODE) { throw 'Could not start schema initialization.' }
$executionName = ($execution | ConvertFrom-Json).name
if (-not $executionName) { throw 'Schema initialization returned no execution name.' }
$deadline = [datetime]::UtcNow.AddMinutes(12)
do {
    $status = az containerapp job execution show --resource-group $rg --name $job `
        --job-execution-name $executionName --query properties.status --output tsv
    if ($LASTEXITCODE) { throw 'Could not inspect schema initialization.' }
    if ($status -eq 'Succeeded') { break }
    if ($status -in @('Failed', 'Stopped')) { throw 'Schema initialization failed; do not deploy apps.' }
    if ([datetime]::UtcNow -ge $deadline) { throw 'Schema initialization timed out; do not deploy apps.' }
    Start-Sleep -Seconds 10
} while ($true)
```

Inspect PostgreSQL startup and db-init logs if this fails, including SMB ownership
or filesystem errors. There is no fixed sleep treated as database readiness:
the application gate is a successful execution of the actual db-init command.

## Deploy applications

Only after that exact job execution succeeds:

```powershell
$env:TF_VAR_initialized_release_tag = $env:TF_VAR_release_tag
Invoke-PocTerraform plan -var-file=poc.local.tfvars -out=apps.tfplan
# Review: public unauthenticated web, internal API and one no-ingress worker.
Invoke-PocTerraform apply apps.tfplan
Remove-Item infra\azure\poc\apps.tfplan
$url = Invoke-PocTerraform output -raw web_url
Invoke-RestMethod "$url/health"
Invoke-RestMethod "$url/api/health"
Remove-Item Env:TF_VAR_database_password
Remove-Item Env:TF_VAR_initialized_release_tag
```

`depends_on` orders resource creation, not migration execution. The
`initialized_release_tag` precondition is an operator attestation, not automatic
Azure execution verification. Never set it before observing `Succeeded`.
The full plan refuses application rollout if it is absent or for another release.
No live Azure deployment or storage durability validation was performed here.

Use HTTPS internal API FQDN routing. ACA HTTP ingress listens on 80/443, not the
container's target port 5000; `http://gridcast-api:5000` from the design sketch
would bypass the ingress port contract.

## Updating and cleanup

Drain the queue and stop old worker revisions before any image update; this PoC
does not implement the staging release script's serialized rollback/snapshot gates.
Repeat image publishing, db-init execution and the full plan with a fresh source
SHA. Do not change the PostgreSQL image, password, mount or template while it is
running: single-revision routing does not guarantee two database processes cannot
overlap during an update. Changing `POSTGRES_PASSWORD` does not rotate an already
initialized PostgreSQL user's password.

The database app, storage account and file share have `prevent_destroy` safeguards.
Cleanup requires explicit review and removal of those guards before `terraform
destroy`. Keep state and a recovery copy of any data you intend to retain.
Do not run the staging backup workflow against this PoC's single share.

## Offline validation

```powershell
Invoke-PocTerraform fmt -check
Invoke-PocTerraform validate
Invoke-PocTerraform test
```

Tests use mocked AzureRM/AzAPI providers, including mocked apply; they do not
contact Azure or execute migrations. They cover ingress boundaries, the single
share, alpine ownership, disabled hosted mode, credential encoding and opt-in
and schema gates.

References: [ACA storage mounts](https://learn.microsoft.com/azure/container-apps/storage-mounts)
and [ACA ingress](https://learn.microsoft.com/azure/container-apps/ingress-overview).
