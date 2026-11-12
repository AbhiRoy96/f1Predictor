---
title: Run the bare minimum Azure PoC
description: Deploy and verify the isolated ACA, ACR and Storage Account PoC using PowerShell and Terraform.
---

## Before you start

Use [Deploy-MinimalPoc.ps1](../scripts/Deploy-MinimalPoc.ps1) to run the
[bare minimum Terraform configuration](../infra/azure/poc/README.md) from
[section 4 of the architecture](POC_README.md#4-bare-minimum-poc-aca-acr--storage-account-only).
It does not call the staging deployment script or use staging state.

> [!WARNING]
> The web app is publicly accessible without sign-in. Anyone with access can
> enqueue work. Use a dedicated resource group and disposable, non-sensitive data.
> PostgreSQL on Azure Files SMB is experimental here; initialization, locking,
> durability and recovery still require live Azure validation.

Install these prerequisites:

* PowerShell 7 (`pwsh`)
* Terraform 1.7 or later
* Azure CLI with the Container Apps extension
* Docker with a running Linux-container engine
* Git, if you omit `-ReleaseTag`
* Azure permissions to create ACA, ACR and Storage resources in your subscription

Have your administrator register `Microsoft.App`, `Microsoft.ContainerRegistry`
and `Microsoft.Storage`. Tenant policy must permit public storage endpoints and
ACR admin credentials. No Entra app registration is needed.

From a PowerShell 7 terminal at the repository root, sign in and install or update
the CLI extension if needed:

```powershell
az login
if ($LASTEXITCODE) { throw 'Azure login failed.' }
az extension add --name containerapp --upgrade
if ($LASTEXITCODE) { throw 'Container Apps extension setup failed.' }
```

The runner scopes every Azure command to `-SubscriptionId`; it does not change
your Azure CLI default subscription. Relative paths in this guide assume the
repository root; the script itself can be called from another working directory.

## First deployment

Build both images and create a dedicated resource group:

```powershell
.\scripts\Deploy-MinimalPoc.ps1 `
    -SubscriptionId '<subscription-guid>' `
    -ResourceGroup 'gridcast-minpoc-rg' `
    -Prefix 'gridcast-minpoc' `
    -Location 'eastus' `
    -CreateResourceGroup `
    -BuildImages `
    -AcknowledgePublicAccess
```

You will be prompted for a database password of at least twenty characters.
Retain it securely: you must supply the **same password** on retries and later
deployments. The runner asks you to review and approve each of three saved
Terraform plans. Enter `y` or `yes` to continue; any other answer stops.

No tfvars file is needed. The runner supplies resource group, prefix, source SHA,
public opt-in and schema gate itself. It does not read `poc.local.tfvars` or the
root `.env`. Do not add automatically loaded `terraform.tfvars` or
`*.auto.tfvars` files to this root: their password values override environment
variables. Use the same group, prefix and local state on every retry.

The default release tag is the checkout's lowercase Git SHA. Keep image contents
consistent with that SHA; commit intended source changes before building.
The prefix is limited to fourteen characters to leave room for app names and a
full SHA revision suffix.

### Restricted Python package access

Pass your approved HTTPS package index explicitly, or set `PIP_INDEX_URL` in the
current PowerShell process. The runner does not load it from `.env`.
For the Microsoft mirror used by the existing local image build:

```powershell
.\scripts\Deploy-MinimalPoc.ps1 `
    -SubscriptionId '<subscription-guid>' `
    -ResourceGroup 'gridcast-minpoc-rg' `
    -BuildImages `
    -PipIndexUrl 'https://packagefeedproxy.microsoft.io/pypi/simple/' `
    -AcknowledgePublicAccess
```

This example assumes the resource group already exists; add
`-CreateResourceGroup` for the first deployment. You can instead use your approved
Azure Artifacts/CFS feed if your Docker builder can authenticate to it.
Do not embed credentials in the URL or disable TLS verification.
Hash enforcement remains enabled by the existing backend Dockerfile.
Without an explicit index, Docker uses its existing default.

### Reuse the images you already built

Omit `-BuildImages` to publish local `gridcast-api:latest` and
`gridcast-web:latest`. Both must exist and be `linux/amd64`; the runner verifies
this before provisioning the foundation. The backend image serves API, worker
and db-init, so separate worker and migration image pushes are unnecessary.

```powershell
.\scripts\Deploy-MinimalPoc.ps1 `
    -SubscriptionId '<subscription-guid>' `
    -ResourceGroup 'gridcast-minpoc-rg' `
    -ReleaseTag '<source-sha-of-local-images>' `
    -AcknowledgePublicAccess
```

Use the SHA that actually matches those images. Do not reuse a deployed tag for
different image contents.

## What the runner does

1. Checks tools, the Azure login, password length and Docker image platform.
2. Initializes and validates only the isolated PoC Terraform root.
3. Optionally builds Linux images and creates the dedicated resource group.
4. Applies the foundation plan for Basic ACR, Storage, one SMB share and the ACA environment.
5. Logs into ACR, tags both images with the source SHA and pushes them.
6. Plans PostgreSQL and the manual init job, stops old worker revisions and waits for their replicas to exit, then applies.
7. Starts db-init and polls that exact execution for `Succeeded`, with a twelve-minute timeout.
8. Sets the matching schema gate and applies the full app plan, without resource targeting.
9. Activates the requested worker revision, waits for all three requested app revisions and checks `/health` and `/api/health`.
10. Prints the public URL and restores any process environment variables it temporarily changed.

The runner rejects deletion/replacement plans and updates to an existing
PostgreSQL app. Saved plans contain secrets and are deleted on normal success or
failure. Local Terraform state also contains secrets; keep it encrypted, backed
up securely and out of Git. Do not run two deployments against the same state
concurrently. If you forcibly terminate PowerShell, securely remove any temporary
plan left behind in your user temporary directory.

## Noninteractive deployment

Use a SecureString from your approved secret-handling mechanism and
`-AutoApprove` to skip plan approval prompts:

```powershell
$password = Read-Host 'Original PoC database password' -AsSecureString
.\scripts\Deploy-MinimalPoc.ps1 `
    -SubscriptionId '<subscription-guid>' `
    -ResourceGroup 'gridcast-minpoc-rg' `
    -DatabasePassword $password `
    -AcknowledgePublicAccess `
    -AutoApprove
```

The password prompt above remains interactive; in automation supply the
SecureString from your secret store instead. Never put a plaintext password on
the command line or in a committed file. `-AutoApprove` does not bypass migration,
plan-safety, readiness or health checks. Failures return a nonzero exit code.

## Retry and troubleshoot

On failure, the runner stops rather than deploying past an unsuccessful step.
Already created Azure resources remain provisioned and billable.
Keep the original password, group, prefix, state and release tag. Correct the
reported issue and rerun. A stopped worker is not automatically restored on a
failed migration; inspect and resolve the schema state before resuming.

Before deploying a new source SHA, drain the job queue and ensure no work is
running. Worker shutdown does not implement a graceful queue drain, snapshots or
rollback. The API/web may remain reachable during migration; prevent new use
during maintenance. See the [Terraform update cautions](../infra/azure/poc/README.md#updating-and-cleanup).

Common failure checks:

* Image missing or wrong platform: rerun with `-BuildImages`.
* Package download blocked: pass an approved reachable index with the locked versions.
* db-init failed: inspect PostgreSQL startup and init execution logs, especially SMB ownership or filesystem errors.
* PostgreSQL plan update rejected: verify you supplied the original password. Image, mount or template maintenance requires a separate reviewed procedure with the database stopped.
* App readiness or health failure: inspect ACA revision logs and internal API/database connectivity; do not treat successful provisioning as a healthy deployment.

To inspect an init execution, use the execution name reported by Azure CLI and
the prefix used for deployment:

```powershell
az containerapp job execution list `
    --subscription '<subscription-guid>' `
    --resource-group 'gridcast-minpoc-rg' `
    --name 'gridcast-minpoc-db-init' `
    --output table
if ($LASTEXITCODE) { throw 'Could not list init executions.' }
```

## Stop and cleanup

There is no automatic shutdown or cleanup. Only PostgreSQL data is persistent;
worker artifacts disappear on replacement. The database, account and share have
Terraform destruction guards. Review the
[cleanup instructions](../infra/azure/poc/README.md#updating-and-cleanup) before
removing resources, and retain any required recovery copy first.
No live Azure initialization or database durability validation is implied by
the offline checks.
