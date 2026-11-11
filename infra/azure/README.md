---
title: Gridcast Azure Container Apps deployment
description: Private staging infrastructure, ordered releases, OIDC CI and required launch drills.
---

## What is deployed

The [foundation](foundation.bicep) creates a VNet-integrated ACA Consumption environment,
private PostgreSQL 17 on port 5432, fourteen-day database backups, a Basic registry,
three worker-only Azure Files shares, a purge-protected RBAC Key Vault, and separate
web/API/worker managed identities. Secret-reader roles are scoped to individual
secrets, not the entire vault. Log retention defaults to thirty days, ingestion to
one GiB/day, and budget notifications to eighty percent actual and one hundred
percent forecasted spend.

The [application template](apps.bicep) provides:

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
variable or template output.

No local Grafana/LGTM service is deployed.

## Prepare a staging foundation

Use a staging subscription/resource group and a region supporting the selected
services. Template compilation is not quota, policy, regional availability or
staging validation.

1. Register a single-tenant Entra web application. Enable the appropriate ID-token
   flow and restrict assignment/access to your intended users.
2. Put its client secret, a random proxy token of at least thirty-two characters,
   and a random database password of at least twenty characters into an existing
   bootstrap Key Vault. Do not put secret values in Git, parameter files or workflow
   variables. ARM deployment needs permission to resolve the bootstrap vault's
   secret references.
3. Copy [foundation.parameters.example.json](foundation.parameters.example.json)
   outside the repository or to an ignored `*.local.parameters.json` file. Replace
   every placeholder, including the first day of the current month and budget
   recipients. Keep the three secrets as Key Vault references.
4. Preview, review and then provision manually when authorized:

```powershell
az bicep build --file infra\azure\foundation.bicep
az deployment group what-if -g $rg -f infra\azure\foundation.bicep --parameters "@$parameters"
az deployment group create -g $rg -n gridcast-foundation -f infra\azure\foundation.bicep --parameters "@$parameters"
```

Name this deployment `<prefix>-foundation`; the release script reads its storage
output to take pre-release snapshots. Foundation provisioning requires resource
creation and role-assignment permissions. Daily releases do not need bootstrap
secret values or Key Vault data access.

The deployment outputs registry/storage/vault/server/environment names and
`entraRedirectUri`. Register that exact URI on the Entra app before releasing web.
Sign-in starts at `/.auth/login/aad`; the API deliberately returns `401`, not HTML
redirects. Test a real tenant sign-in and callback in staging.

Never deploy the application template with `isWebExternal=true` on first creation.
ACA authentication is a child resource, so simultaneous external ingress and auth
creation can otherwise leave a transient unauthenticated window.

## Build and release

Use the existing test and scan workflows before approving a release. Images must
be `linux/amd64` and tagged with the full source SHA:

```powershell
az acr build -r $acr --platform linux/amd64 -t "gridcast-api:$sha" .
az acr build -r $acr --platform linux/amd64 -t "gridcast-web:$sha" .\web
```

The [release script](../../scripts/Deploy-Aca.ps1) defaults to a non-mutating
application `what-if`. Foundation resources and built images must already exist:

```powershell
.\scripts\Deploy-Aca.ps1 -ResourceGroup $rg -Prefix gridcast -ReleaseTag $sha -EntraClientId $clientId -EntraTenantId $tenantId
.\scripts\Deploy-Aca.ps1 -ResourceGroup $rg -Prefix gridcast -ReleaseTag $sha -EntraClientId $clientId -EntraTenantId $tenantId -Operation Deploy -StatePath .\release-state.json
```

Drain the queue and wait for any active forecast before approving deployment.
The release then:

1. Saves previous images/revisions and makes existing web ingress internal.
2. Deactivates old worker revisions and waits for their replicas to stop.
3. Snapshots existing data/models/reports shares and records the snapshot responses.
4. Runs the new DB-init job and waits for that exact execution to succeed.
5. Deploys the applications with web still internal and a fresh revision suffix.
6. Waits for the requested revisions, verifies Entra configuration, then enables web.
7. Requires web health `200` and anonymous API `401`; on smoke failure, closes web ingress.

Failures are not success-shaped fallbacks: the script aborts, and may deliberately
leave web internal and worker stopped. Inspect the release-state artifact, job logs
and revision health before retrying. Do not bypass these gates to restore traffic.
Schema changes must remain additive; adopt a migration tool before destructive changes.

## OIDC workflow

The [release workflow](../../.github/workflows/release.yml) is manual and defaults
to `Preview`. Configure a protected GitHub environment named `staging` with approval
rules and an Entra OIDC federated credential restricted to this repository/environment.
Use a least-privileged deployment identity, not a stored Azure password.

Configure these environment variables:

- `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`, `ACA_PREFIX`, `ACR_NAME`, `ENTRA_CLIENT_ID`
- For backups: `AZURE_STORAGE_ACCOUNT`, `AZURE_BACKUPS_ENABLED=true`
- Optional `AZURE_SNAPSHOT_RETENTION_DAYS`, default fourteen

The release identity needs app/job/deployment management, managed-identity assignment,
registry remote-build permissions if requested, and storage share snapshot permissions.
Runtime identities already have only `AcrPull` and their individual secret-reader roles.
Do not grant the release principal Key Vault secret read access merely for image rollout.
Review the effective role scopes against your tenant policy before deployment.

Optional non-secret settings may be committed as `infra/azure/staging.parameters.json`,
based on [apps.parameters.example.json](apps.parameters.example.json). Publication
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
.\scripts\Deploy-Aca.ps1 -ResourceGroup $rg -Prefix gridcast -ReleaseTag $previousSha -EntraClientId $clientId -EntraTenantId $tenantId -Operation Rollback -StatePath .\rollback-state.json
```

Rollback skips migration execution; it never reverses schema changes or overwrites
model artifacts. The previous application must support the current additive schema.
Worker shutdown, snapshots, auth verification and smoke gates still apply.

## Dependency reproducibility

The backend Dockerfile and Linux CI install [requirements.lock](../../requirements.lock)
with `--require-hashes`; [requirements.txt](../../requirements.txt) remains the editable
development input. The initial lock contains seventy-nine exact versions from the
existing consistent Linux Python 3.12 runtime, hashed from PyPI, with every direct
constraint and installed dependency validated. No TLS checks were disabled.

Refresh using a trusted network:

```powershell
python -m uv pip compile requirements.txt --python-version 3.12 --python-platform x86_64-unknown-linux-gnu --generate-hashes -o requirements.lock
```

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
