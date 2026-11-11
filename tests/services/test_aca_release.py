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
        harness = r"""
. '__SCRIPT__' -ResourceGroup test -Prefix gridcast -ReleaseTag ('a' * 40) `
    -EntraClientId '00000000-0000-0000-0000-000000000001' `
    -EntraTenantId '00000000-0000-0000-0000-000000000002' -Operation __OPERATION__
$Calls = [System.Collections.Generic.List[string]]::new()
$Failure = '__FAILURE__'
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
    if ($Call -like 'deployment group show*') { return 'sttest' }
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
        harness = harness.replace("__SCRIPT__", script).replace("__OPERATION__", operation).replace("__FAILURE__", failure)
        completed = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", harness],
            check=True, text=True, capture_output=True, timeout=30,
        )
        return json.loads(completed.stdout)

    def test_preview_has_no_mutating_commands(self):
        result = self.run_release("Preview")
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["calls"]), 1)
        self.assertIn("what-if", result["calls"][0])

    def test_release_orders_migration_worker_and_auth_before_exposure(self):
        result = self.run_release()
        self.assertIsNone(result["error"])
        calls = result["calls"]
        stop = next(i for i, call in enumerate(calls) if "revision deactivate" in call)
        migrate = next(i for i, call in enumerate(calls) if "job start" in call)
        apps = next(i for i, call in enumerate(calls) if "group create" in call and "shouldDeployApps=true" in call)
        auth = next(i for i, call in enumerate(calls) if call.startswith("rest "))
        expose = next(i for i, call in enumerate(calls) if "--type external" in call)
        self.assertLess(stop, migrate)
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
        self.assertFalse(any("shouldDeployApps=true" in call and "group create" in call for call in result["calls"]))
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
        self.assertTrue(any("group create" in call and "shouldDeployApps=true" in call for call in result["calls"]))


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
