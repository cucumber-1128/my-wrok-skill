# Deterministic supervision + pending startup/outbox reconciliation.
# No target means supervise this state directory's registered sessions only.
param(
    [string]$Leader = "",
    [string]$Watchdog = "",
    [string]$Router = "router",
    [string]$StateFile = "",
    [string]$StateDir = "",
    [int]$IntervalSec = 30,
    [int]$WatchdogIntervalSec = 0,
    [int]$GraceSec = 60,
    [switch]$Once
)
. (Join-Path $PSScriptRoot 'sa-entry.ps1')
$runtimeArgs = @()
if ($StateDir) { $runtimeArgs += @('--state-dir', $StateDir) }
$runtimeArgs += @('watchdog', '--router', $Router, '--interval-sec', "$IntervalSec",
                  '--watchdog-interval-sec', "$WatchdogIntervalSec", '--grace-sec', "$GraceSec")
if ($Leader) { $runtimeArgs += @('--leader', $Leader) }
if ($Watchdog) { $runtimeArgs += @('--watchdog', $Watchdog) }
if ($StateFile) { $runtimeArgs += @('--state-file', $StateFile) }
if ($Once) { $runtimeArgs += '--once' }
Invoke-HerdrRuntime -RuntimeArgs $runtimeArgs
exit $script:HerdrRuntimeExitCode
