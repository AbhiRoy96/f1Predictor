"""Offline infrastructure and fail-closed release contracts; never contacts Azure."""

import json
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("pwsh")


class LockContracts(unittest.TestCase):
    def test_runtime_lock_pins_and_hashes_every_direct_dependency(self):
        lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
        packages = {}
        for block in re.split(r"(?m)^(?=[a-zA-Z0-9_.-]+==)", lock)[1:]:
            name, version = block.splitlines()[0].split("==", 1)
            self.assertTrue(version.rstrip(" \\"))
            self.assertRegex(block, r"--hash=sha256:[a-f0-9]{64}")
            self.assertNotRegex(block, r"--hash=(?!sha256:|sha384:|sha512:)")
            packages[name.lower().replace("_", "-")] = version
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.startswith("#"):
                name = re.match(r"[a-zA-Z0-9_.-]+", line).group().lower().replace("_", "-")
                self.assertIn(name, packages)
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("--require-hashes -r requirements.lock", dockerfile)
        self.assertNotIn(".copilot", lock)


@unittest.skipUnless(POWERSHELL, "PowerShell 7 is needed for offline release orchestration tests")
class ReleaseContracts(unittest.TestCase):
    def run_release(self, operation="Deploy", failure=""):
        script = str(ROOT / "scripts" / "Deploy-Aca.ps1").replace("'", "''")
        parameters = str(ROOT / "infra" / "azure" / "apps" / "terraform.tfvars.example").replace("'", "''")
        harness = r"""
. '__SCRIPT__' -ResourceGroup test -Prefix gridcast -ReleaseTag ('a' * 40) `
    -EntraClientId '00000000-0000-0000-0000-000000000001' `
    -EntraTenantId '00000000-0000-0000-0000-000000000002' -Operation __OPERATION__ `
    -ParametersFile '__PARAMETERS__'
$Calls = [System.Collections.Generic.List[string]]::new()
$Failure = '__FAILURE__'
$PlanKinds = @{}
function Invoke-Terraform {
    param([string[]]$Arguments, [switch]$AsJson)
    if ($Arguments[0] -eq 'apply') {
        $Calls.Add("terraform apply $($PlanKinds[$Arguments[-1]])")
        if ($Failure -eq 'apply') { throw 'Terraform failed: apply.' }
        return
    }
    $Calls.Add("terraform $($Arguments -join ' ')")
    if ($Arguments[0] -eq 'plan') {
        if ($Failure -eq 'plan') { throw 'Terraform failed: plan.' }
        $Path = ($Arguments | Where-Object { $_ -like '-out=*' }).Substring(5)
        $PlanKinds[$Path] = $(if ($Arguments -contains '-target=azapi_resource.init') {'init'} else {'apps'})
    }
    if ($Arguments[0] -eq 'show') {
        return [pscustomobject]@{
            resource_changes=@(@{change=@{actions=$(if ($Failure -eq 'replacement') {@('delete','create')} else {@('update')})}})
            planned_values=@{outputs=@{storage_name=@{value='sttest'}}}
        }
    }
}
function Invoke-AzJson {
    param([string[]]$Arguments)
    $Call = $Arguments -join ' '
    $Calls.Add($Call)
    if ($Call -eq 'containerapp list -g test') {
        return @('web','worker') | ForEach-Object {
            [pscustomobject]@{
                name="gridcast-$_"
                properties=@{latestReadyRevisionName="old-$_";template=@{containers=@(@{image="image:old"})}}
            }
        }
    }
    if ($Call -like 'containerapp revision list*') {
        return [pscustomobject]@{name='old-worker';properties=@{active=$true}}
    }
    if ($Call -like 'containerapp replica list*') { return }
    if ($Call -like 'storage share-rm snapshot*') {
        return [pscustomobject]@{name=$Arguments[-3];snapshotTime='2026-11-11T00:00:00Z'}
    }
    if ($Call -like 'containerapp job start*') { return [pscustomobject]@{name='migration-run'} }
    if ($Call -like 'containerapp job execution show*') {
        return [pscustomobject]@{properties=@{status=$(if ($Failure -eq 'migration') {'Failed'} else {'Succeeded'})}}
    }
    if ($Call -like 'containerapp show*') {
        return [pscustomobject]@{id='/subscriptions/test/containerApps/gridcast-web'
            properties=@{configuration=@{ingress=@{fqdn='web.example'}}}}
    }
    if ($Arguments[0] -eq 'rest') {
        return [pscustomobject]@{properties=@{
            platform=@{enabled=($Failure -ne 'auth')}
            globalValidation=@{unauthenticatedClientAction='Return401'}
            identityProviders=@{azureActiveDirectory=@{enabled=$true;registration=@{
                clientId='00000000-0000-0000-0000-000000000001'
                openIdIssuer='https://login.microsoftonline.com/00000000-0000-0000-0000-000000000002/v2.0'
            }}}
        }}
    }
}
function Wait-AcaReady {
    param([string]$Name,[string]$RevisionSuffix)
    $Calls.Add("ready $Name $RevisionSuffix")
}
function Invoke-WebRequest {
    param([string]$Uri,[switch]$SkipHttpErrorCheck,[int]$TimeoutSec,[int]$MaximumRedirection)
    $Calls.Add("http $Uri")
    return [pscustomobject]@{StatusCode=$(if ($Uri.EndsWith('/health') -or $Failure -eq 'smoke') {200} else {401})}
}
function Start-Sleep { param([int]$Seconds) }
try {
    $State = Invoke-AcaRelease
    @{state=$State;calls=$Calls;error=$null} | ConvertTo-Json -Depth 10 -Compress
} catch {
    @{state=$null;calls=$Calls;error=$_.Exception.Message} | ConvertTo-Json -Depth 10 -Compress
}
"""
        harness = (harness.replace("__SCRIPT__", script).replace("__PARAMETERS__", parameters)
                   .replace("__OPERATION__", operation).replace("__FAILURE__", failure))
        completed = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", harness],
            check=True, text=True, capture_output=True, timeout=30,
        )
        return json.loads(completed.stdout)

    def test_preview_has_no_mutating_commands(self):
        result = self.run_release("Preview")
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["calls"]), 3)
        self.assertIn("terraform validate", result["calls"][0])
        self.assertIn("terraform plan", result["calls"][1])
        self.assertIn("terraform show -json", result["calls"][2])

    def test_release_orders_migration_worker_and_auth_before_exposure(self):
        result = self.run_release()
        self.assertIsNone(result["error"])
        calls = result["calls"]
        stop = next(i for i, call in enumerate(calls) if "revision deactivate" in call)
        migrate = next(i for i, call in enumerate(calls) if "job start" in call)
        init = calls.index("terraform apply init")
        apps = calls.index("terraform apply apps")
        auth = next(i for i, call in enumerate(calls) if call.startswith("rest "))
        expose = next(i for i, call in enumerate(calls) if "--type external" in call)
        self.assertLess(stop, migrate)
        self.assertLess(init, migrate)
        self.assertLess(migrate, apps)
        self.assertLess(auth, expose)
        self.assertTrue(any("replica list" in call for call in calls))
        snapshots = [i for i, call in enumerate(calls) if "share-rm snapshot" in call]
        self.assertEqual(len(snapshots), 3)
        self.assertLess(stop, min(snapshots))
        self.assertLess(max(snapshots), migrate)
        self.assertEqual(result["state"]["previous"][1]["revision"], "old-worker")
        self.assertNotEqual(result["state"]["revisionSuffix"], result["state"]["releaseTag"])

    def test_migration_failure_prevents_app_update_and_exposure(self):
        result = self.run_release(failure="migration")
        self.assertIn("initialization failed", result["error"])
        self.assertNotIn("terraform apply apps", result["calls"])
        self.assertFalse(any("--type external" in call for call in result["calls"]))

    def test_auth_failure_never_enables_external_ingress(self):
        result = self.run_release(failure="auth")
        self.assertIn("Entra configuration verification failed", result["error"])
        self.assertFalse(any("--type external" in call for call in result["calls"]))

    def test_failed_public_smoke_closes_ingress(self):
        result = self.run_release(failure="smoke")
        self.assertIn("smoke test failed", result["error"])
        self.assertIn("--type internal", result["calls"][-1])

    def test_rollback_uses_previous_images_without_reversing_schema(self):
        result = self.run_release("Rollback")
        self.assertIsNone(result["error"])
        self.assertFalse(any("job start" in call for call in result["calls"]))
        self.assertIn("terraform apply apps", result["calls"])
        self.assertNotIn("terraform apply init", result["calls"])

    def test_plan_failures_or_replacements_abort_before_cloud_mutation(self):
        for failure in ("plan", "replacement"):
            with self.subTest(failure=failure):
                result = self.run_release(failure=failure)
                self.assertIsNotNone(result["error"])
                self.assertTrue(all(call.startswith("terraform ") for call in result["calls"]))
                self.assertFalse(any(call.startswith("terraform apply") for call in result["calls"]))

    def test_apply_failure_never_starts_migration_or_exposes_web(self):
        result = self.run_release(failure="apply")
        self.assertIn("Terraform failed: apply", result["error"])
        self.assertFalse(any("job start" in call or "--type external" in call for call in result["calls"]))


@unittest.skipUnless(POWERSHELL, "PowerShell 7 is needed for offline PoC runner tests")
class MinimalPocContracts(unittest.TestCase):
    def run_poc(self, scenario="success"):
        script = str(ROOT / "scripts" / "Deploy-MinimalPoc.ps1").replace("'", "''")
        harness = r"""
. '__SCRIPT__' -SubscriptionId '00000000-0000-0000-0000-000000000001' `
    -ResourceGroup test-poc -Prefix gridcast-poc -AcknowledgePublicAccess -AutoApprove `
    -DatabasePassword (ConvertTo-SecureString ('p' * 24) -AsPlainText -Force)
$Scenario = '__SCENARIO__'
$Calls = [System.Collections.Generic.List[string]]::new()
$Plans = @{}
$NativeInvoker = ${function:Invoke-PocNative}
$RevisionWaiter = ${function:Wait-PocRevision}
$Polls = 0
$ReadyPolls = 0
$ActivePolls = 0
$env:ARM_SUBSCRIPTION_ID = 'previous-subscription'
$env:ARM_TENANT_ID = 'previous-tenant'
$env:TF_VAR_database_password = 'previous-password'
$env:TF_VAR_initialized_release_tag = 'untrusted-old-gate'
$PipIndexUrl = ''
if ($Scenario -eq 'no-opt-in') { $AcknowledgePublicAccess = $false }
if ($Scenario -eq 'declined-plan') { $AutoApprove = $false }
if ($Scenario -eq 'build') {
    $BuildImages = $true
    $PipIndexUrl = 'https://packages.example/simple/'
}
function Get-Command { param([string]$Name) }
function Invoke-PocNative {
    param([string]$Command, [string[]]$Arguments, [switch]$Capture)
    $Call = "$Command $($Arguments -join ' ')"
    $Calls.Add($Call)
    if ($Command -eq 'git') { return ('a' * 40) }
    if ($Command -eq 'docker') {
        if ($Arguments[0] -eq 'push' -and $Scenario -eq 'push-failure') { throw 'docker push failed' }
        if ($Call -like '*image inspect*') {
            if ($Scenario -eq 'wrong-platform') { return 'linux/arm64' }
            return 'linux/amd64'
        }
        return
    }
    if ($Command -eq 'terraform') {
        $Subcommand = $Arguments[1]
        if ($Subcommand -eq 'plan') {
            $PlanPath = ($Arguments | Where-Object { $_ -like '-out=*' }).Substring(5)
            $Plans[$PlanPath] = if ($Arguments -contains '-target=azapi_resource.init') { 'init' }
                elseif ($Arguments -contains '-target=azurerm_container_registry.main') { 'foundation' }
                else { 'apps' }
        }
        if ($Subcommand -eq 'show') {
            $Kind = $Plans[$Arguments[-1]]
            $Actions = @('create')
            $Address = 'azurerm_container_registry.main'
            if ($Scenario -eq 'replacement') { $Actions = @('delete', 'create') }
            if ($Scenario -eq 'database-update' -and $Kind -eq 'init') {
                $Actions = @('update')
                $Address = 'azapi_resource.postgres'
            }
            return (@{resource_changes=@(@{address=$Address;change=@{actions=$Actions}})} |
                ConvertTo-Json -Depth 10 -Compress)
        }
        if ($Subcommand -eq 'apply') {
            $Calls.Add("apply $($Plans[$Arguments[-1]])")
            return
        }
        if ($Subcommand -eq 'output') {
            return @{
                registry_name='crtest';registry_login_server='crtest.azurecr.io'
                init_job_name='gridcast-poc-db-init';web_url='https://poc.example'
            }[$Arguments[-1]]
        }
        return
    }
    if ($Call -like 'az acr login*') {
        if ($Capture) { return 'Login Succeeded' }
        Write-Host 'Login Succeeded'
        return
    }
    if ($Call -like 'az account show*') { return '{"tenantId":"test-tenant"}' }
    if ($Call -like 'az containerapp list*') {
        if ($Scenario -eq 'first-deployment') { return '[]' }
        return '[{"name":"gridcast-poc-worker"}]'
    }
    if ($Call -like 'az containerapp revision list*') {
        return '[{"name":"old-worker","properties":{"active":true}}]'
    }
    if ($Call -like 'az containerapp show*') {
        $script:ReadyPolls++
        $Revision = if ($script:ReadyPolls -eq 1) { 'old-worker' } else { 'gridcast-poc-worker--' + ('a' * 40) }
        return (@{properties=@{provisioningState='Succeeded';latestReadyRevisionName=$Revision}} |
            ConvertTo-Json -Compress)
    }
    if ($Call -like 'az containerapp revision show*') {
        $script:ActivePolls++
        return (@{properties=@{active=($script:ActivePolls -gt 1)}} | ConvertTo-Json -Compress)
    }
    if ($Call -like 'az containerapp replica list*') {
        if ($Scenario -eq 'readiness') { return '[{"name":"new-worker-replica"}]' }
        return '[]'
    }
    if ($Call -like 'az containerapp job start*') {
        if ($Scenario -eq 'missing-execution') { return '{}' }
        return '{"name":"current-init-execution"}'
    }
    if ($Call -like 'az containerapp job execution show*') {
        if ($Scenario -eq 'migration-failure') { return '{"properties":{"status":"Failed"}}' }
        $script:Polls++
        if ($script:Polls -eq 1) { return '{"properties":{"status":"Running"}}' }
        return '{"properties":{"status":"Succeeded"}}'
    }
    return '{}'
}
function Wait-PocRevision {
    param([string]$Name, [string]$Tag)
    $Calls.Add("ready $Name--$Tag")
}
function Invoke-WebRequest {
    param([string]$Uri, [switch]$SkipHttpErrorCheck, [int]$TimeoutSec)
    $Calls.Add("http $Uri")
    return [pscustomobject]@{StatusCode=$(if ($Scenario -eq 'health-failure') {503} else {200})}
}
function Start-Sleep { param([int]$Seconds) }
function Read-Host { param([string]$Prompt) return 'n' }
$Url = $null
$Failure = $null
try {
    if ($Scenario -eq 'native-failure') {
        & $NativeInvoker -Command '__POWERSHELL__' -Arguments @('-NoProfile', '-Command', 'exit 7') -Capture
    } elseif ($Scenario -eq 'readiness') {
        & $RevisionWaiter -Name 'gridcast-poc-worker' -Tag ('a' * 40)
    } else {
        $Url = Invoke-PocDeployment
    }
} catch { $Failure = $_.Exception.Message }
@{
    url=$Url;error=$Failure;calls=$Calls
    environment=@($env:ARM_SUBSCRIPTION_ID,$env:ARM_TENANT_ID,
        $env:TF_VAR_database_password,$env:TF_VAR_initialized_release_tag)
    remainingPlans=@($Plans.Keys | Where-Object { Test-Path -LiteralPath $_ })
} | ConvertTo-Json -Depth 10 -Compress
"""
        harness = (harness.replace("__SCRIPT__", script).replace("__SCENARIO__", scenario)
                   .replace("__POWERSHELL__", POWERSHELL.replace("'", "''")))
        completed = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", harness],
            check=True, text=True, capture_output=True, timeout=30,
        )
        return json.loads(completed.stdout.splitlines()[-1])

    def test_orders_bootstrap_push_stop_migration_and_full_rollout(self):
        result = self.run_poc()
        self.assertIsNone(result["error"])
        calls = result["calls"]
        foundation = calls.index("apply foundation")
        pushes = [i for i, call in enumerate(calls) if call.startswith("docker push")]
        stop = next(i for i, call in enumerate(calls) if "revision deactivate" in call)
        init = calls.index("apply init")
        polls = [i for i, call in enumerate(calls) if "job execution show" in call]
        apps = calls.index("apply apps")
        activate = next(i for i, call in enumerate(calls) if "revision activate" in call)
        self.assertEqual(len(pushes), 2)
        self.assertLess(foundation, min(pushes))
        self.assertLess(max(pushes), stop)
        self.assertLess(stop, init)
        self.assertEqual(len(polls), 2)
        self.assertLess(init, min(polls))
        self.assertLess(max(polls), apps)
        self.assertLess(apps, activate)
        self.assertTrue(all("--job-execution-name current-init-execution" in calls[i] for i in polls))
        plans = [call for call in calls if " plan " in call]
        self.assertIn("-var=initialized_release_tag=" + "a" * 40, plans[-1])
        self.assertNotIn("-target=", plans[-1])
        self.assertFalse(any("untrusted-old-gate" in call for call in calls))
        self.assertTrue(all("--subscription " in call for call in calls if call.startswith("az ")))
        self.assertEqual(result["url"], "https://poc.example")
        self.assertEqual(calls[-2:], ["http https://poc.example/health", "http https://poc.example/api/health"])
        self.assertEqual(len([call for call in calls if call.startswith("ready ")]), 3)

    def test_first_deployment_and_optional_linux_builds(self):
        for scenario in ("first-deployment", "build"):
            with self.subTest(scenario=scenario):
                result = self.run_poc(scenario)
                self.assertIsNone(result["error"])
                if scenario == "first-deployment":
                    self.assertFalse(any("revision deactivate" in call for call in result["calls"]))
                else:
                    builds = [call for call in result["calls"] if call.startswith("docker build")]
                    self.assertEqual(len(builds), 2)
                    self.assertTrue(all("--platform linux/amd64" in call for call in builds))
                    self.assertIn("PIP_INDEX_URL=https://packages.example/simple/", builds[0])

    def test_failures_block_later_stages_and_restore_credentials(self):
        for scenario in ("push-failure", "migration-failure", "missing-execution",
                         "replacement", "database-update", "health-failure", "declined-plan"):
            with self.subTest(scenario=scenario):
                result = self.run_poc(scenario)
                self.assertIsNotNone(result["error"])
                self.assertIsNone(result["url"])
                self.assertEqual(result["remainingPlans"], [])
                self.assertEqual(result["environment"], [
                    "previous-subscription", "previous-tenant", "previous-password", "untrusted-old-gate",
                ])
                if scenario != "health-failure":
                    self.assertNotIn("apply apps", result["calls"])
                if scenario in ("replacement", "database-update"):
                    self.assertFalse(any("revision deactivate" in call for call in result["calls"]))
                if scenario == "push-failure":
                    self.assertNotIn("apply init", result["calls"])
                if scenario == "declined-plan":
                    self.assertFalse(any(call.startswith("apply ") for call in result["calls"]))

    def test_opt_in_and_platform_checks_precede_provisioning(self):
        for scenario in ("no-opt-in", "wrong-platform"):
            with self.subTest(scenario=scenario):
                result = self.run_poc(scenario)
                self.assertIsNotNone(result["error"])
                self.assertNotIn("apply foundation", result["calls"])
                self.assertFalse(any(call.startswith("az group create") for call in result["calls"]))
        self.assertEqual(self.run_poc("no-opt-in")["calls"], [])

    def test_native_nonzero_exit_is_not_treated_as_success(self):
        result = self.run_poc("native-failure")
        self.assertIn("exit code 7", result["error"])

    def test_readiness_waits_past_stale_and_inactive_revisions(self):
        result = self.run_poc("readiness")
        self.assertIsNone(result["error"])
        calls = result["calls"]
        self.assertEqual(len([call for call in calls if call.startswith("az containerapp show")]), 3)
        revisions = [call for call in calls if call.startswith("az containerapp revision show")]
        self.assertEqual(len(revisions), 2)
        self.assertTrue(all("--revision gridcast-poc-worker--" + "a" * 40 in call for call in revisions))


@unittest.skipUnless(POWERSHELL, "PowerShell 7 is needed for offline snapshot retention tests")
class SnapshotContracts(unittest.TestCase):
    def run_snapshots(self, snapshots, retention="14"):
        workflow = (ROOT / ".github" / "workflows" / "backups.yml").read_text(encoding="utf-8")
        block = re.search(r"(?ms)^        run: \|\n(.*?)(?=^      -|\Z)", workflow)
        self.assertIsNotNone(block)
        script = textwrap.dedent(block.group(1))
        existing = json.dumps(snapshots).replace("'", "''")
        with tempfile.TemporaryDirectory() as temporary:
            path = temporary.replace("'", "''")
            harness = r"""
$env:RUNNER_TEMP = '__TEMP__'
$env:RESOURCE_GROUP = 'test'
$env:STORAGE_ACCOUNT = 'sttest'
$env:RETENTION_DAYS = '__RETENTION__'
$Calls = [System.Collections.Generic.List[string]]::new()
function az {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
    $Calls.Add(($Arguments -join ' '))
    $global:LASTEXITCODE = 0
    if ($Arguments[2] -eq 'snapshot') { return '{"snapshotTime":"2000-01-01T00:00:00Z"}' }
    if ($Arguments[2] -eq 'list') { return '__EXISTING__' }
}
try {
    & {
__SCRIPT__
    }
    @{calls=$Calls;error=$null} | ConvertTo-Json -Compress
} catch {
    @{calls=$Calls;error=$_.Exception.Message} | ConvertTo-Json -Compress
}
"""
            harness = (harness.replace("__TEMP__", path).replace("__RETENTION__", retention)
                       .replace("__EXISTING__", existing).replace("__SCRIPT__", script))
            completed = subprocess.run(
                [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", harness],
                check=True, text=True, capture_output=True, timeout=30,
            )
        return json.loads(completed.stdout)

    def test_retention_deletes_only_old_marked_snapshots_on_allowed_shares(self):
        marked = {"gridcast_backup": "true"}
        old = "2000-01-01T00:00:00Z"
        result = self.run_snapshots([
            {"name": "data", "snapshotTime": old, "metadata": marked},
            {"name": "models", "properties": {"snapshotTime": old, "metadata": marked}},
            {"name": "reports", "snapshotTime": "2999-01-01T00:00:00Z", "metadata": marked},
            {"name": "reports", "snapshotTime": old, "metadata": {}},
            {"name": "unrelated", "snapshotTime": old, "metadata": marked},
            {"name": "data", "metadata": {}},
        ])
        self.assertIsNone(result["error"])
        deleted = [call for call in result["calls"] if call.startswith("storage share-rm delete ")]
        self.assertEqual(len(deleted), 2)
        for name, call in zip(("data", "models"), deleted):
            self.assertIn(f"--name {name} --snapshot {old} --yes", call)

    def test_marked_backup_without_timestamp_refuses_deletion(self):
        result = self.run_snapshots([{"name": "data", "metadata": {"gridcast_backup": "true"}}])
        self.assertIn("refusing deletion", result["error"])
        self.assertFalse(any("share-rm delete" in call for call in result["calls"]))

    def test_invalid_retention_refuses_deletion(self):
        for retention in ("6", "91", "invalid"):
            with self.subTest(retention=retention):
                result = self.run_snapshots([], retention)
                self.assertIn("seven to ninety days", result["error"])
                self.assertFalse(any("share-rm delete" in call for call in result["calls"]))


if __name__ == "__main__":
    unittest.main()
