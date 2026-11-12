#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#Requires -Version 7.0
<#
.SYNOPSIS
    Build, publish and deploy the isolated bare minimum Terraform PoC.
.DESCRIPTION
    Apply reviewed foundation and database plans, wait for the exact db-init execution,
    then deploy and health-check applications. Never uses the staging Terraform roots.
.PARAMETER SubscriptionId
    Target Azure subscription, without changing the Azure CLI default subscription.
.PARAMETER ResourceGroup
    Dedicated PoC resource group. Never use the staging resource group.
.PARAMETER Prefix
    Short lowercase PoC resource prefix, at most fourteen characters.
.PARAMETER ReleaseTag
    Immutable source SHA. Defaults to the current checkout's HEAD.
.PARAMETER DatabasePassword
    Existing or new database password as a SecureString. Prompts securely when omitted.
.PARAMETER AcknowledgePublicAccess
    Required acknowledgement that web has public unauthenticated access.
.PARAMETER CreateResourceGroup
    Create the dedicated resource group before planning.
.PARAMETER Location
    Region used only when creating the resource group.
.PARAMETER BuildImages
    Build both linux/amd64 images. Otherwise publish existing gridcast-*:latest images.
.PARAMETER PipIndexUrl
    Approved HTTPS package index for backend builds. Defaults to PIP_INDEX_URL in this process.
.PARAMETER AutoApprove
    Apply checked saved plans without the interactive approval at each stage.
.EXAMPLE
    ./scripts/Deploy-MinimalPoc.ps1 -SubscriptionId <id> -ResourceGroup gridcast-poc-rg -CreateResourceGroup -BuildImages -AcknowledgePublicAccess
.NOTES
    Requires PowerShell 7, Terraform 1.7+, Azure CLI with Container Apps support,
    Docker and an Azure login. Local Terraform state contains secrets.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][guid]$SubscriptionId,
    [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$ResourceGroup,
    [ValidatePattern('^[a-z][a-z0-9-]{2,13}$')][string]$Prefix = 'gridcast-minpoc',
    [ValidatePattern('^[a-f0-9]{7,40}$')][string]$ReleaseTag,
    [securestring]$DatabasePassword,
    [switch]$AcknowledgePublicAccess,
    [switch]$CreateResourceGroup,
    [ValidateNotNullOrEmpty()][string]$Location = 'eastus',
    [switch]$BuildImages,
    [string]$PipIndexUrl = $env:PIP_INDEX_URL,
    [switch]$AutoApprove
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$RepoRoot = Split-Path $PSScriptRoot -Parent
$PocDirectory = Join-Path $RepoRoot 'infra\azure\poc'

#region Functions
function Invoke-PocNative {
    <#
    .SYNOPSIS
        Run a native command and reject a nonzero exit without logging its arguments.
    .OUTPUTS
        Captured text when requested, otherwise console output.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([string]$Command, [string[]]$Arguments, [switch]$Capture)
    if ($Capture) {
        $Result = & $Command @Arguments
    }
    else {
        & $Command @Arguments | ForEach-Object { Write-Host $_ }
    }
    if ($LASTEXITCODE -ne 0) { throw "$Command failed (exit code $LASTEXITCODE); deployment stopped." }
    if ($Capture) { return ($Result -join "`n").Trim() }
}

function Invoke-PocTerraform {
    <#
    .SYNOPSIS
        Run Terraform only against the isolated PoC root.
    .OUTPUTS
        Captured text when requested.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([string[]]$Arguments, [switch]$Capture)
    Invoke-PocNative -Command terraform -Arguments (@("-chdir=$PocDirectory") + $Arguments) -Capture:$Capture
}

function Invoke-PocAz {
    <#
    .SYNOPSIS
        Run subscription-scoped Azure CLI commands and parse JSON without displaying secrets.
    .OUTPUTS
        Parsed JSON, or null for commands without output.
    #>
    [CmdletBinding()]
    [OutputType([object])]
    param([string[]]$Arguments)
    $Result = Invoke-PocNative -Command az -Capture -Arguments (
        $Arguments + @('--subscription', "$SubscriptionId", '--only-show-errors', '--output', 'json')
    )
    if ($Result) { return ($Result | ConvertFrom-Json -Depth 100) }
}

function Stop-PocWorker {
    <#
    .SYNOPSIS
        Stop existing worker revisions before schema changes and wait for replicas to exit.
    .OUTPUTS
        None.
    #>
    [CmdletBinding()]
    [OutputType([void])]
    param()
    $Existing = @(Invoke-PocAz @('containerapp', 'list', '-g', $ResourceGroup))
    if (@($Existing | ForEach-Object { $_.name }) -notcontains "$Prefix-worker") { return }
    $Revisions = @(Invoke-PocAz @('containerapp', 'revision', 'list', '-g', $ResourceGroup, '-n', "$Prefix-worker"))
    foreach ($Revision in $Revisions | Where-Object { $_.properties.active }) {
        Invoke-PocAz @('containerapp', 'revision', 'deactivate', '-g', $ResourceGroup,
            '-n', "$Prefix-worker", '--revision', $Revision.name) | Out-Null
        $Deadline = [datetime]::UtcNow.AddMinutes(3)
        do {
            $Replicas = @(Invoke-PocAz @('containerapp', 'replica', 'list', '-g', $ResourceGroup,
                '-n', "$Prefix-worker", '--revision', $Revision.name))
            if ($Replicas.Count -eq 0) { break }
            if ([datetime]::UtcNow -ge $Deadline) { throw 'Old worker replicas did not stop; deployment stopped.' }
            Start-Sleep -Seconds 5
        } while ($true)
    }
}

function Invoke-PocPlan {
    <#
    .SYNOPSIS
        Review and apply a saved stage plan, rejecting deletions and existing database changes.
    .OUTPUTS
        None.
    #>
    [CmdletBinding()]
    [OutputType([void])]
    param(
        [ValidateSet('Foundation', 'Init', 'Apps')][string]$Stage,
        [string[]]$VariableArguments,
        [string]$InitializedTag = ''
    )
    $PlanPath = [System.IO.Path]::GetTempFileName()
    try {
        Write-Host "Planning PoC stage: $Stage"
        $Arguments = @('plan', '-input=false', '-no-color', "-out=$PlanPath",
            "-var=initialized_release_tag=$InitializedTag") + $VariableArguments
        if ($Stage -eq 'Foundation') {
            $Arguments += @('-target=azurerm_container_registry.main',
                '-target=azurerm_container_app_environment_storage.main')
        }
        elseif ($Stage -eq 'Init') { $Arguments += '-target=azapi_resource.init' }
        Invoke-PocTerraform $Arguments
        $Plan = Invoke-PocTerraform @('show', '-json', $PlanPath) -Capture | ConvertFrom-Json -Depth 100
        foreach ($Change in $Plan.resource_changes) {
            if ($Change.change.actions -contains 'delete') {
                throw 'Plan includes deletion or replacement; resolve it manually before deployment.'
            }
            if ($Change.address -eq 'azapi_resource.postgres' -and $Change.change.actions -contains 'update') {
                throw 'Plan changes the existing PostgreSQL app. Stop and review database maintenance manually.'
            }
        }
        if (-not $AutoApprove -and (Read-Host "Apply the $Stage plan? [y/N]") -notin @('y', 'yes')) {
            throw "$Stage plan not approved; deployment stopped."
        }
        if ($Stage -eq 'Init') { Stop-PocWorker }
        Invoke-PocTerraform @('apply', '-input=false', '-no-color', $PlanPath)
    }
    finally {
        if (Test-Path -LiteralPath $PlanPath) { Remove-Item -LiteralPath $PlanPath }
    }
}

function Wait-PocInitialization {
    <#
    .SYNOPSIS
        Wait for the execution started by this deployment, never an older successful run.
    .OUTPUTS
        None.
    #>
    [CmdletBinding()]
    [OutputType([void])]
    param([string]$JobName, [string]$ExecutionName)
    if (-not $ExecutionName) { throw 'Database initialization returned no execution name.' }
    $Deadline = [datetime]::UtcNow.AddMinutes(12)
    do {
        $Run = Invoke-PocAz @('containerapp', 'job', 'execution', 'show', '-g', $ResourceGroup,
            '-n', $JobName, '--job-execution-name', $ExecutionName)
        if ($Run.properties.status -eq 'Succeeded') { return }
        if ($Run.properties.status -in @('Failed', 'Stopped')) {
            throw "Database initialization $ExecutionName failed; applications were not deployed."
        }
        if ([datetime]::UtcNow -ge $Deadline) { throw 'Database initialization timed out; applications were not deployed.' }
        Start-Sleep -Seconds 10
    } while ($true)
}

function Wait-PocRevision {
    <#
    .SYNOPSIS
        Verify the requested revision is ready and active with at least one replica.
    .OUTPUTS
        None.
    #>
    [CmdletBinding()]
    [OutputType([void])]
    param([string]$Name, [string]$Tag)
    $RevisionName = "$Name--$Tag"
    $Deadline = [datetime]::UtcNow.AddMinutes(10)
    do {
        $App = Invoke-PocAz @('containerapp', 'show', '-g', $ResourceGroup, '-n', $Name)
        if ($App.properties.provisioningState -eq 'Failed') { throw "$Name provisioning failed." }
        if ($App.properties.latestReadyRevisionName -eq $RevisionName) {
            $Revision = Invoke-PocAz @('containerapp', 'revision', 'show', '-g', $ResourceGroup,
                '-n', $Name, '--revision', $RevisionName)
            $Replicas = @(Invoke-PocAz @('containerapp', 'replica', 'list', '-g', $ResourceGroup,
                '-n', $Name, '--revision', $RevisionName))
            if ($Revision.properties.active -and $Replicas.Count -gt 0) { return }
        }
        if ([datetime]::UtcNow -ge $Deadline) { throw "$RevisionName did not become ready within ten minutes." }
        Start-Sleep -Seconds 10
    } while ($true)
}

function Invoke-PocDeployment {
    <#
    .SYNOPSIS
        Execute the checked PoC deployment and restore process-level credentials on every exit.
    .OUTPUTS
        Public web URL after both health checks succeed.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param()
    if (-not $AcknowledgePublicAccess) {
        throw 'Pass -AcknowledgePublicAccess only for a disposable PoC with unauthenticated public access.'
    }
    foreach ($Tool in @('terraform', 'az', 'docker')) { Get-Command $Tool -ErrorAction Stop | Out-Null }
    $Tag = $ReleaseTag
    if (-not $Tag) {
        $Tag = Invoke-PocNative -Command git -Arguments @('-C', $RepoRoot, 'rev-parse', 'HEAD') -Capture
    }
    if ($Tag -notmatch '^[a-f0-9]{7,40}$') { throw 'ReleaseTag must be a lowercase source SHA of 7 to 40 characters.' }
    if ($PipIndexUrl) {
        $Index = [uri]$PipIndexUrl
        if (-not $Index.IsAbsoluteUri -or $Index.Scheme -ne 'https' -or $Index.UserInfo) {
            throw 'PipIndexUrl must be an absolute HTTPS URL without embedded credentials.'
        }
    }
    $Password = $DatabasePassword
    if (-not $Password) { $Password = Read-Host 'PoC database password (reuse the original on retries)' -AsSecureString }
    $SavedEnvironment = @{}
    foreach ($Name in @('ARM_SUBSCRIPTION_ID', 'ARM_TENANT_ID', 'TF_VAR_database_password')) {
        $SavedEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
    }
    try {
        $env:TF_VAR_database_password = [System.Net.NetworkCredential]::new('', $Password).Password
        if ($env:TF_VAR_database_password.Length -lt 20) { throw 'Database password must contain at least twenty characters.' }
        $env:ARM_SUBSCRIPTION_ID = "$SubscriptionId"
        $Account = Invoke-PocAz @('account', 'show')
        $env:ARM_TENANT_ID = $Account.tenantId
        Invoke-PocNative -Command docker -Arguments @('info') -Capture | Out-Null
        Invoke-PocTerraform @('init', '-input=false', '-no-color')
        Invoke-PocTerraform @('validate', '-no-color')
        $Variables = @("-var=resource_group_name=$ResourceGroup", "-var=prefix=$Prefix",
            "-var=release_tag=$Tag", '-var=is_public_poc=true')
        if ($BuildImages) {
            $BuildArguments = @('build', '--platform', 'linux/amd64', '--tag', 'gridcast-api:latest',
                '--file', (Join-Path $RepoRoot 'Dockerfile'))
            if ($PipIndexUrl) { $BuildArguments += @('--build-arg', "PIP_INDEX_URL=$PipIndexUrl") }
            Invoke-PocNative -Command docker -Arguments ($BuildArguments + @($RepoRoot))
            Invoke-PocNative -Command docker -Arguments @('build', '--platform', 'linux/amd64',
                '--tag', 'gridcast-web:latest', (Join-Path $RepoRoot 'web'))
        }
        foreach ($Image in @('gridcast-api:latest', 'gridcast-web:latest')) {
            $Platform = Invoke-PocNative -Command docker -Capture -Arguments @(
                'image', 'inspect', '--format', '{{.Os}}/{{.Architecture}}', $Image)
            if ($Platform -ne 'linux/amd64') { throw "$Image must be linux/amd64; use -BuildImages." }
        }
        if ($CreateResourceGroup) {
            Invoke-PocAz @('group', 'create', '--name', $ResourceGroup, '--location', $Location) | Out-Null
        }
        else { Invoke-PocAz @('group', 'show', '--name', $ResourceGroup) | Out-Null }
        Invoke-PocPlan -Stage Foundation -VariableArguments $Variables
        $Registry = Invoke-PocTerraform @('output', '-raw', 'registry_name') -Capture
        $Server = Invoke-PocTerraform @('output', '-raw', 'registry_login_server') -Capture
        Invoke-PocNative -Command az -Arguments @('acr', 'login', '--name', $Registry,
            '--subscription', "$SubscriptionId", '--only-show-errors')
        foreach ($Image in @('gridcast-api', 'gridcast-web')) {
            $RemoteImage = "${Server}/${Image}:$Tag"
            Invoke-PocNative -Command docker -Arguments @('tag', "${Image}:latest", $RemoteImage)
            Invoke-PocNative -Command docker -Arguments @('push', $RemoteImage)
        }
        Invoke-PocPlan -Stage Init -VariableArguments $Variables
        $Job = Invoke-PocTerraform @('output', '-raw', 'init_job_name') -Capture
        $Execution = Invoke-PocAz @('containerapp', 'job', 'start', '-g', $ResourceGroup, '-n', $Job)
        if (-not $Execution -or -not $Execution.PSObject.Properties['name']) {
            throw 'Database initialization returned no execution name.'
        }
        Wait-PocInitialization -JobName $Job -ExecutionName $Execution.name
        Invoke-PocPlan -Stage Apps -VariableArguments $Variables -InitializedTag $Tag
        # A retry of the same tag can have a no-op worker plan after its revision was stopped.
        Invoke-PocAz @('containerapp', 'revision', 'activate', '-g', $ResourceGroup,
            '-n', "$Prefix-worker", '--revision', "$Prefix-worker--$Tag") | Out-Null
        foreach ($Kind in @('api', 'worker', 'web')) {
            Wait-PocRevision -Name "$Prefix-$Kind" -Tag $Tag
        }
        $Url = Invoke-PocTerraform @('output', '-raw', 'web_url') -Capture
        foreach ($Path in @('/health', '/api/health')) {
            $Response = Invoke-WebRequest "$Url$Path" -SkipHttpErrorCheck -TimeoutSec 30
            if ($Response.StatusCode -ne 200) { throw "PoC smoke test failed: $Path returned $($Response.StatusCode)." }
        }
        return $Url
    }
    finally {
        foreach ($Name in $SavedEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($Name, $SavedEnvironment[$Name], 'Process')
        }
    }
}
#endregion Functions

#region Main Execution
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $Url = Invoke-PocDeployment
        Write-Host "PoC ready (public, no sign-in): $Url"
    }
    catch {
        Write-Error -ErrorAction Continue "PoC deployment failed: $($_.Exception.Message)"
        exit 1
    }
}
#endregion Main Execution
