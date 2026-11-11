#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#Requires -Version 7.0
<#
.SYNOPSIS
    Preview or release immutable Gridcast images to an existing ACA foundation.
.DESCRIPTION
    Default operation only previews. Deployment initializes the database before app rollout,
    stops the old worker, keeps web ingress internal until Entra is verified, and smoke-tests
    anonymous access. Rollback uses an explicit previous SHA and never reverses migrations.
.PARAMETER ResourceGroup
    Existing foundation resource group.
.PARAMETER Prefix
    Prefix used by foundation.bicep.
.PARAMETER ReleaseTag
    Git SHA tagging both previously built linux/amd64 images.
.PARAMETER EntraClientId
    Single-tenant web application client ID.
.PARAMETER EntraTenantId
    Issuing Entra tenant ID.
.PARAMETER Operation
    Preview, Deploy, or Rollback. Preview is the safe default.
.PARAMETER ParametersFile
    Optional ARM parameter file for non-secret app settings such as schedules and OTLP.
.PARAMETER StatePath
    Optional output path for the previous release metadata. No secret values are recorded.
.EXAMPLE
    ./scripts/Deploy-Aca.ps1 -ResourceGroup staging -Prefix gridcast -ReleaseTag abc1234 -EntraClientId <id> -EntraTenantId <id>
.NOTES
    Requires Azure CLI with containerapp support and an existing foundation deployment.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$ResourceGroup,
    [Parameter(Mandatory)][ValidatePattern('^[a-z][a-z0-9-]{2,11}$')][string]$Prefix,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{7,40}$')][string]$ReleaseTag,
    [Parameter(Mandatory)][guid]$EntraClientId,
    [Parameter(Mandatory)][guid]$EntraTenantId,
    [ValidateSet('Preview', 'Deploy', 'Rollback')][string]$Operation = 'Preview',
    [string]$ParametersFile,
    [string]$StatePath
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

#region Functions
function Invoke-AzJson {
    <#
    .SYNOPSIS
        Execute Azure CLI and reject native-command failures.
    .OUTPUTS
        Parsed JSON, or null for empty output.
    #>
    [CmdletBinding()]
    [OutputType([object])]
    param([Parameter(Mandatory)][string[]]$Arguments)
    $Result = & az @Arguments --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw "Azure CLI failed: $($Arguments[0..1] -join ' ')." }
    if ($Result) { return ($Result -join "`n" | ConvertFrom-Json) }
}

function Wait-AcaReady {
    <#
    .SYNOPSIS
        Wait for the requested revision rather than a stale healthy revision.
    .OUTPUTS
        None.
    #>
    [CmdletBinding()]
    [OutputType([void])]
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$RevisionSuffix
    )
    $Deadline = [datetime]::UtcNow.AddMinutes(10)
    do {
        $App = Invoke-AzJson @('containerapp', 'show', '-g', $ResourceGroup, '-n', $Name)
        if ($App.properties.provisioningState -eq 'Failed') { throw "$Name provisioning failed." }
        if ($App.properties.latestReadyRevisionName -eq "$Name--$RevisionSuffix") { return }
        Start-Sleep -Seconds 10
    } while ([datetime]::UtcNow -lt $Deadline)
    throw "$Name did not make revision $RevisionSuffix ready within ten minutes."
}

function Invoke-AcaRelease {
    <#
    .SYNOPSIS
        Execute an ordered, fail-closed application release.
    .OUTPUTS
        Release metadata with prior images and revisions, never secret values.
    #>
    [CmdletBinding()]
    [OutputType([object])]
    param()
    $Template = Join-Path (Split-Path $PSScriptRoot -Parent) 'infra\azure\apps.bicep'
    $RevisionSuffix = "$($ReleaseTag.Substring(0, 7))-$([datetime]::UtcNow.ToString('yyyyMMddHHmmss'))"
    $DeploymentParameters = @()
    if ($ParametersFile) { $DeploymentParameters += "@$((Resolve-Path $ParametersFile).Path)" }
    $DeploymentParameters += @(
        "prefix=$Prefix", "releaseTag=$ReleaseTag", "revisionSuffix=$RevisionSuffix", "entraClientId=$EntraClientId",
        "entraTenantId=$EntraTenantId", 'isWebExternal=false'
    )
    $Base = @('deployment', 'group')
    Invoke-AzJson ($Base + @('what-if', '-g', $ResourceGroup, '-f', $Template,
        '--parameters') + $DeploymentParameters + @('shouldDeployApps=true')) | Out-Null
    if ($Operation -eq 'Preview') {
        return [pscustomobject]@{ operation = 'Preview'; releaseTag = $ReleaseTag }
    }

    $Existing = @(Invoke-AzJson @('containerapp', 'list', '-g', $ResourceGroup))
    $ExistingNames = @($Existing | ForEach-Object { $_.name })
    $Previous = @($Existing | Where-Object { $_.name -in @("$Prefix-web", "$Prefix-api", "$Prefix-worker") } |
        ForEach-Object {
            [pscustomobject]@{
                name = $_.name
                revision = $_.properties.latestReadyRevisionName
                image = $_.properties.template.containers[0].image
            }
        })
    $State = [pscustomobject]@{
        operation = $Operation; releaseTag = $ReleaseTag; revisionSuffix = $RevisionSuffix; previous = $Previous
    }
    if ($StatePath) { $State | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $StatePath -Encoding utf8 }

    if ($ExistingNames -contains "$Prefix-web") {
        Invoke-AzJson @('containerapp', 'ingress', 'enable', '-g', $ResourceGroup,
            '-n', "$Prefix-web", '--type', 'internal', '--target-port', '3000', '--transport', 'http') | Out-Null
    }
    if ($ExistingNames -contains "$Prefix-worker") {
        $Revisions = @(Invoke-AzJson @('containerapp', 'revision', 'list', '-g',
            $ResourceGroup, '-n', "$Prefix-worker"))
        foreach ($Revision in $Revisions | Where-Object { $_.properties.active }) {
            Invoke-AzJson @('containerapp', 'revision', 'deactivate', '-g', $ResourceGroup,
                '-n', "$Prefix-worker", '--revision', $Revision.name) | Out-Null
            $Deadline = [datetime]::UtcNow.AddMinutes(3)
            do {
                $Replicas = @(Invoke-AzJson @('containerapp', 'replica', 'list', '-g', $ResourceGroup,
                    '-n', "$Prefix-worker", '--revision', $Revision.name))
                if ($Replicas.Count -eq 0) { break }
                if ([datetime]::UtcNow -ge $Deadline) { throw 'Old worker replicas did not stop; rollout aborted.' }
                Start-Sleep -Seconds 5
            } while ($true)
        }
    }
    if ($Previous.Count -gt 0) {
        $StorageName = Invoke-AzJson @('deployment', 'group', 'show', '-g', $ResourceGroup,
            '-n', "$Prefix-foundation", '--query', 'properties.outputs.storageName.value')
        if (-not $StorageName) { throw 'Foundation storage output is missing; cannot snapshot before release.' }
        $Snapshots = foreach ($Share in @('data', 'models', 'reports')) {
            Invoke-AzJson @('storage', 'share-rm', 'snapshot', '-g', $ResourceGroup,
                '--storage-account', $StorageName, '--name', $Share, '--metadata', 'gridcast_backup=true')
        }
        $State | Add-Member -NotePropertyName snapshots -NotePropertyValue @($Snapshots)
        if ($StatePath) { $State | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $StatePath -Encoding utf8 }
    }
    if ($Operation -eq 'Deploy') {
        Invoke-AzJson ($Base + @('create', '-g', $ResourceGroup, '-n', "$Prefix-init",
            '-f', $Template, '--parameters') + $DeploymentParameters + @('shouldDeployApps=false')) | Out-Null
        $Execution = Invoke-AzJson @('containerapp', 'job', 'start', '-g', $ResourceGroup, '-n', "$Prefix-db-init")
        $Deadline = [datetime]::UtcNow.AddMinutes(12)
        do {
            $Run = Invoke-AzJson @('containerapp', 'job', 'execution', 'show', '-g', $ResourceGroup,
                '-n', "$Prefix-db-init", '--job-execution-name', $Execution.name)
            if ($Run.properties.status -eq 'Succeeded') { break }
            if ($Run.properties.status -in @('Failed', 'Stopped')) { throw 'DB initialization failed; applications were not updated.' }
            if ([datetime]::UtcNow -ge $Deadline) { throw 'DB initialization timed out; applications were not updated.' }
            Start-Sleep -Seconds 10
        } while ($true)
    }
    Invoke-AzJson ($Base + @('create', '-g', $ResourceGroup, '-n', "$Prefix-apps",
        '-f', $Template, '--parameters') + $DeploymentParameters + @('shouldDeployApps=true')) | Out-Null
    foreach ($Kind in @('api', 'web', 'worker')) { Wait-AcaReady "$Prefix-$Kind" -RevisionSuffix $RevisionSuffix }
    $Web = Invoke-AzJson @('containerapp', 'show', '-g', $ResourceGroup, '-n', "$Prefix-web")
    $Config = Invoke-AzJson @('rest', '--method', 'get', '--url',
        "$($Web.id)/authConfigs/current?api-version=2026-07-01")
    if (-not $Config.properties.platform.enabled -or
        $Config.properties.globalValidation.unauthenticatedClientAction -ne 'Return401' -or
        -not $Config.properties.identityProviders.azureActiveDirectory.enabled -or
        $Config.properties.identityProviders.azureActiveDirectory.registration.clientId -ne "$EntraClientId" -or
        $Config.properties.identityProviders.azureActiveDirectory.registration.openIdIssuer -notlike "*/$EntraTenantId/v2.0") {
        throw 'Entra configuration verification failed; web remains internal.'
    }
    Invoke-AzJson @('containerapp', 'ingress', 'enable', '-g', $ResourceGroup,
        '-n', "$Prefix-web", '--type', 'external', '--target-port', '3000', '--transport', 'http') | Out-Null
    try {
        $Web = Invoke-AzJson @('containerapp', 'show', '-g', $ResourceGroup, '-n', "$Prefix-web")
        $Url = "https://$($Web.properties.configuration.ingress.fqdn)"
        $Health = Invoke-WebRequest "$Url/health" -SkipHttpErrorCheck -TimeoutSec 30
        $Anonymous = Invoke-WebRequest "$Url/api/jobs" -SkipHttpErrorCheck -MaximumRedirection 0 -TimeoutSec 30
        if ($Health.StatusCode -ne 200 -or $Anonymous.StatusCode -ne 401) {
            throw 'Hosted smoke test failed: expected health 200 and anonymous API 401.'
        }
        $State | Add-Member -NotePropertyName webUrl -NotePropertyValue $Url
        if ($StatePath) { $State | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $StatePath -Encoding utf8 }
        return $State
    }
    catch {
        Invoke-AzJson @('containerapp', 'ingress', 'enable', '-g', $ResourceGroup,
            '-n', "$Prefix-web", '--type', 'internal', '--target-port', '3000', '--transport', 'http') | Out-Null
        throw
    }
}
#endregion Functions

#region Main Execution
if ($MyInvocation.InvocationName -ne '.') {
    try {
        Invoke-AcaRelease | ConvertTo-Json -Depth 10
        exit 0
    }
    catch {
        Write-Error -ErrorAction Continue "ACA release failed: $($_.Exception.Message) Web may remain internal and the worker stopped. Inspect the saved release state before retrying or rolling back."
        exit 1
    }
}
#endregion Main Execution
