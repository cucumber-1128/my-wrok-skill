# Durable per-object event polling. Configuration commands are user-owned code.
param(
    [string]$Config = "$env:USERPROFILE\.config\herdr-core\triggers.json",
    [string]$StateDir = "",
    [int]$IntervalSec = 5,
    [switch]$Once
)
. (Join-Path $PSScriptRoot 'sa-entry.ps1')
$runtimeArgs = @()
if ($StateDir) { $runtimeArgs += @('--state-dir', $StateDir) }
$runtimeArgs += @('trigger', '--config', $Config, '--interval-sec', "$IntervalSec")
if ($Once) { $runtimeArgs += '--once' }
Invoke-HerdrRuntime -RuntimeArgs $runtimeArgs
exit $script:HerdrRuntimeExitCode
