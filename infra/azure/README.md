---
title: Gridcast Azure Container Apps deployment
description: Private staging infrastructure, ordered releases, OIDC CI and required launch drills.
---

## What is deployed

The [Terraform foundation](foundation/main.tf) creates a VNet-integrated ACA Consumption environment,
private PostgreSQL 17 on port 5432, fourteen-day database backups, a Basic registry,
three worker-only Azure Files shares, a purge-protected RBAC Key Vault, and separate
web/API/worker managed identities. Secret-reader roles are scoped to individual
secrets, not the entire vault. Log retention defaults to thirty days, ingestion to
one GiB/day, and budget notifications to eighty percent actual and one hundred
percent forecasted spend.

The [Terraform applications](apps/main.tf) provide:

- Internal-only API, externally authenticated web, and a no-ingress worker
- Worker min/max one replica, UID/GID 10001 SMB mount options, and local FastF1 cache
- A manual schema-init job, run before every normal application release
- Entra authentication returning `401` to unauthenticated requests, except `/health`
- Optional enqueue-only scheduled jobs with genuine event/publication timestamps
- Optional controlled HTTP/protobuf OTLP endpoint, otherwise telemetry disabled
- Ten-percent trace sampling, disabled profiling/GPU metrics and HTTP probes

SMB mounts require a storage account key, so shared-key access is intentionally
enabled only on this dedicated storage account. Its file endpoint allows the ACA
subnet and Azure trusted services, not arbitrary public clients. The key stays in
the managed environment storage registration and is never an application environment
variable or Terraform output. Terraform foundation state does contain the storage
key, database password and bootstrap secret values. Treat state and saved plans as
secrets even though Terraform redacts sensitive values from normal console output.

No local Grafana/LGTM service is deployed.

## Prepare a staging foundation

Use a staging subscription/resource group and a region supporting the selected
services. Install Terraform 1.7 or later (1.7 is required for the offline mock tests)
and Azure CLI. Terraform validation is not quota, policy, regional availability or
staging validation. Register `Microsoft.App`, `Microsoft.Network`,
`Microsoft.DBforPostgreSQL`, `Microsoft.ContainerRegistry`, `Microsoft.ManagedIdentity`,
`Microsoft.KeyVault`, `Microsoft.Storage`, `Microsoft.OperationalInsights` and
`Microsoft.Consumption` with your subscription administrator; providers do not
automatically register services.

Create a separate Azure Blob state account before initializing Terraform. Enable
encryption, blob versioning and soft delete, restrict network access to deployment
runners, and use Entra authentication (no storage keys in backend files). Create
separate `foundation-state` and `apps-state` containers. Grant the release identity
Storage Blob Data Contributor on **only the apps container**, never the foundation
container. Azure Blob leases provide Terraform state locking. Serialize all releases
and foundation changes; state locks cover individual commands, not the entire
multi-stage release.

1. Register a single-tenant Entra web application. Enable the appropriate ID-token
   flow and restrict assignment/access to your intended users.
2. Put its client secret, a random proxy token of at least thirty-two characters,
   and a random database password of at least twenty characters into an existing
   bootstrap Key Vault. Do not put secret values in Git, parameter files or workflow
   variables. Use the bootstrap names `gridcast-database-password`,
   `gridcast-proxy-token` and `gridcast-entra-client-secret`. The foundation operator
   needs secret-read permission on this bootstrap vault; the release identity does not.
3. Copy [foundation/terraform.tfvars.example](foundation/terraform.tfvars.example)
   to an ignored `foundation.local.tfvars` file. Replace
   every placeholder, including the first day of the current month and budget
   recipients. Only the bootstrap vault ID is configured, not secret values.
   Copy [foundation/backend.hcl.example](foundation/backend.hcl.example) to an ignored
   `foundation.local.hcl` and configure the foundation state container.
4. Preview, review and then provision manually when authorized:

```powershell
az login
$env:ARM_SUBSCRIPTION_ID = "<staging-subscription-id>"
terraform -chdir=infra\azure\foundation init -backend-config="<absolute-path-to-foundation.local.hcl>"
terraform -chdir=infra\azure\foundation validate
terraform -chdir=infra\azure\foundation plan -var-file="<absolute-path-to-foundation.local.tfvars>" -out=foundation.tfplan
terraform -chdir=infra\azure\foundation apply foundation.tfplan
terraform -chdir=infra\azure\foundation output
```

Review the plan before applying and securely remove the saved plan afterward.
Foundation provisioning requires resource creation and role-assignment permissions.
Daily releases do not need bootstrap secret values, foundation state or Key Vault
data access. AzureRM manages the foundation; AzAPI handles management-plane secret
writes and file shares, and application resources including auth and SMB mount options.
Both roots commit provider lock files; upgrade them deliberately with `terraform init -upgrade`.

The deployment outputs registry/storage/vault/server/environment names and
`entra_redirect_uri`. Register that exact URI on the Entra app before releasing web.
Sign-in starts at `/.auth/login/aad`; the API deliberately returns `401`, not HTML
redirects. Test a real tenant sign-in and callback in staging.

Terraform always configures web ingress as internal. Only the release script
enables external ingress after verifying the auth child resource. Do not manually
apply applications in place of the ordered release script.

These configurations target a new staging foundation. They are not an automatic
state migration from ARM deployments: generated names differ from the former Bicep
names. For any existing deployment, first inventory resources, align names and import
them into the correct Terraform state under operator review. Do not apply a fresh
state to existing live resources or accept replacement of the database or shares.

## Build and release

Use the existing test and scan workflows before approving a release. Images must
be `linux/amd64` and tagged with the full source SHA:

```powershell
az acr build -r $acr --platform linux/amd64 -t "gridcast-api:$sha" .
az acr build -r $acr --platform linux/amd64 -t "gridcast-web:$sha" .\web
```

Copy [apps/terraform.tfvars.example](apps/terraform.tfvars.example) to an ignored
`apps.local.tfvars` and fill in the foundation's registry, vault and storage names.
Copy [apps/backend.hcl.example](apps/backend.hcl.example) to `apps.local.hcl`, using
the apps state container, and initialize it once per runner/workspace:

```powershell
terraform -chdir=infra\azure\apps init -backend-config="<absolute-path-to-apps.local.hcl>"
$appParameters = "<absolute-path-to-apps.local.tfvars>"
```

The [release script](../../scripts/Deploy-Aca.ps1) defaults to a non-mutating
application `terraform plan`, preceded by `terraform validate`. Foundation resources
and built images must already exist:

```powershell
.\scripts\Deploy-Aca.ps1 -ResourceGroup $rg -Prefix gridcast -ReleaseTag $sha -EntraClientId $clientId -EntraTenantId $tenantId -ParametersFile $appParameters
.\scripts\Deploy-Aca.ps1 -ResourceGroup $rg -Prefix gridcast -ReleaseTag $sha -EntraClientId $clientId -EntraTenantId $tenantId -ParametersFile $appParameters -Operation Deploy -StatePath .\release-state.json
```

Drain the queue and wait for any active forecast before approving deployment.
The release then:

1. Saves previous images/revisions and makes existing web ingress internal.
2. Deactivates old worker revisions and waits for their replicas to stop.
3. Snapshots existing data/models/reports shares and records the snapshot responses.
4. Applies only the DB-init job, runs it and waits for that exact execution to succeed.
5. Replans and applies the applications with web internal and a fresh revision suffix.
6. Waits for the requested revisions, verifies Entra configuration, then enables web.
7. Requires web health `200` and anonymous API `401`; on smoke failure, closes web ingress.

Failures are not success-shaped fallbacks: the script aborts, and may deliberately
leave web internal and worker stopped. Inspect the release-state artifact, job logs
and revision health before retrying. Do not bypass these gates to restore traffic.
Schema changes must remain additive; adopt a migration tool before destructive changes.
The init stage intentionally targets `azapi_resource.init`; it does not toggle app
resource counts or remove existing apps from state. Every apply uses its exact saved
plan; temporary plans are removed afterward. Release plans containing deletion or
replacement are rejected before application. Removing scheduled jobs therefore needs
a separate reviewed Terraform maintenance operation. External ingress is intentionally
an imperative post-auth gate; a later plan shows it returning to internal.

## OIDC workflow

The [release workflow](../../.github/workflows/release.yml) is manual and defaults
to `Preview`. Configure a protected GitHub environment named `staging` with approval
rules and an Entra OIDC federated credential restricted to this repository/environment.
Use a least-privileged deployment identity, not a stored Azure password.

Configure these environment variables:

- `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`, `ACA_PREFIX`, `ACR_NAME`, `ENTRA_CLIENT_ID`
- `AZURE_KEY_VAULT`, `AZURE_STORAGE_ACCOUNT` from the foundation outputs
- `TF_STATE_RESOURCE_GROUP`, `TF_STATE_STORAGE_ACCOUNT`, `TF_STATE_CONTAINER`, `TF_STATE_KEY`
- For backups: `AZURE_STORAGE_ACCOUNT`, `AZURE_BACKUPS_ENABLED=true`
- Optional `AZURE_SNAPSHOT_RETENTION_DAYS`, default fourteen

Use `apps-state` for `TF_STATE_CONTAINER`, never `foundation-state`. The workflow
uses the same federated identity for Terraform providers and the Azure Blob backend.

The release identity needs app/job management, foundation resource metadata read,
managed-identity assignment, access to the apps state container,
registry remote-build permissions if requested, and storage share snapshot permissions.
Runtime identities already have only `AcrPull` and their individual secret-reader roles.
Do not grant the release principal Key Vault secret read access merely for image rollout.
Review the effective role scopes against your tenant policy before deployment.

Optional non-secret settings may be committed as `infra/azure/staging.tfvars.json`,
for example `{"otlp_endpoint": null, "scheduled_forecasts": []}`. Resource names default
to the workflow environment variables; never commit secret values. See
[apps/variables.tf](apps/variables.tf) for schedule fields. Publication
times and schedules are per-event operator inputs, not inferred official-grid data.
Empty schedules disable automatic forecasts. No scheduled job mounts worker artifacts.

## Backups and rollback

PostgreSQL point-in-time restore retention is configured by the foundation. The
[snapshot workflow](../../.github/workflows/backups.yml) is disabled until
`AZURE_BACKUPS_ENABLED=true`; it snapshots the three shares daily and prunes only
its marked snapshots older than the configured retention. Its OIDC management-plane
calls do not retrieve storage keys or require public SMB access.
The workflow uses PowerShell 7.5 or later to preserve snapshot timestamps as strings
when parsing JSON; snapshot identifiers must not be reformatted as local dates.

Daily share snapshots are crash-consistent, not a transaction spanning three shares.
Schedule them while idle or quiesce the worker for an application-consistent recovery
point. Pre-release snapshots are taken after worker shutdown; draining first avoids
capturing an interrupted file write. A snapshot in the same storage account is not
an off-account disaster-recovery backup.

Test a PostgreSQL point-in-time restore into a *new* staging server. Restore selected
Azure Files snapshots into *new* shares, validate Parquet/model manifests and UID
10001 write permissions, then point isolated staging apps at the restored resources.
Never practice restores over the live database/shares. Document timestamps and
recovery results; no restore drill has been performed by this repository change.

Retain previous protected image tags and release-state artifacts. Roll back by
redeploying a known previous SHA with a fresh revision suffix:

```powershell
.\scripts\Deploy-Aca.ps1 -ResourceGroup $rg -Prefix gridcast -ReleaseTag $previousSha -EntraClientId $clientId -EntraTenantId $tenantId -ParametersFile $appParameters -Operation Rollback -StatePath .\rollback-state.json
```

Rollback skips migration execution; it never reverses schema changes or overwrites
model artifacts. The previous application must support the current additive schema.
Worker shutdown, snapshots, auth verification and smoke gates still apply.

## Dependency reproducibility

The backend Dockerfile and Linux CI install [requirements.lock](../../requirements.lock)
with `--require-hashes`; [requirements.txt](../../requirements.txt) remains the editable
development input. The lock contains seventy-nine exact versions, resolved for
Linux Python 3.12 against the selected Microsoft package mirror with SHA-256 hashes.
Relative to the original lock, mirror availability requires Click `8.5.0`,
NCCL `2.32.3`, Pydantic `2.14.2`, pydantic-core `2.50.2` and threadpoolctl `3.7.0`.
Other package versions, including XGBoost, remain unchanged. No TLS or hash checks
are disabled.

Set `PIP_INDEX_URL` to your approved index in your local shell and in the ignored
root `.env` for Compose. The Dockerfile and Compose build already support this
override. Do not put credentials in the URL or build arguments; authenticated feeds
require separate secret-based configuration. Your team's CFS-backed Azure Artifacts
feed takes precedence where required by organizational policy.

Refresh using your approved index:

```powershell
python -m uv pip compile requirements.txt --python-version 3.12 --python-platform x86_64-unknown-linux-gnu --generate-hashes --no-annotate --index-url $env:PIP_INDEX_URL -o requirements.lock
$lock = Get-Content requirements.lock | Where-Object { $_ -notmatch '^\s+--hash=md5:' }
$lock | Set-Content -Encoding utf8 requirements.lock
python -m unittest tests.services.test_aca_release.LockContracts -v
```

The mirror can advertise MD5 entries alongside SHA-256; pip rejects MD5 even when
strong hashes are also present. The normalization removes only MD5 entries, and
the lock contract verifies that every package retains SHA-256 hashes.

Rebuild, run tests and scans, and retrain models after updates. Artifact loading rejects
Python/library version mismatches. Reproducible package versions do not pin the base
OS image; review base-image scan results on each release.

## Launch evidence still required

No Azure provisioning, image push, tenant sign-in, scheduled execution or live model
evaluation was performed here. Before public forecasts, verify:

- Entra callback/access policy, anonymous `401`, and external API unreachability
- UID 10001 mount writes, ephemeral cache behavior and a real forecast's peak resources
- Database/share restore drills, backup retention and a rollback drill
- Traces and metrics from both Gunicorn workers with your controlled exporter
- Full recent data, window ablation, locked final test and a saved reference evaluation
- Upstream data/trademark permissions for the intended use
