# Checked role startup. -NoWait persists a pending task; -Resume continues it.
param(
    [string]$Role = "",
    [Parameter(Mandatory = $true)][string]$Name,
    [string]$Cwd = (Get-Location).Path,
    [string]$Task = "",
    [string]$Model = "",
    [string]$RoleDir = "",
    [int]$ReadyTimeoutSec = 90,
    [string]$StateDir = "",
    [string]$ThreadId = "",
    [string]$TaskId = "",
    [string]$ParentAddress = "",
    [switch]$NoWait,
    [switch]$Resume,
    [switch]$ReplaceOwner,
    [string]$WorkflowProfile,
    [string]$Purpose
)
. (Join-Path $PSScriptRoot 'sa-entry.ps1')
$runtimeArgs = @()
if ($StateDir) { $runtimeArgs += @('--state-dir', $StateDir) }
$runtimeArgs += @('spawn', '--name', $Name, '--cwd', $Cwd, '--ready-timeout', "$ReadyTimeoutSec")
if ($Role) { $runtimeArgs += @('--role', $Role) }
if ($Task) { $runtimeArgs += @('--task', $Task) }
if ($Model) { $runtimeArgs += @('--model', $Model) }
if ($PSBoundParameters.ContainsKey('WorkflowProfile')) { $runtimeArgs += @('--workflow-profile', $WorkflowProfile) }
if ($PSBoundParameters.ContainsKey('Purpose')) { $runtimeArgs += @('--purpose', $Purpose) }
if ($RoleDir) { $runtimeArgs += @('--role-dir', $RoleDir) }
if ($ThreadId) { $runtimeArgs += @('--thread', $ThreadId) }
if ($TaskId) { $runtimeArgs += @('--task-id', $TaskId) }
if ($ParentAddress) { $runtimeArgs += @('--parent', $ParentAddress) }
if ($NoWait) { $runtimeArgs += '--no-wait' }
if ($Resume) { $runtimeArgs += '--resume' }
if ($ReplaceOwner) { $runtimeArgs += '--replace-owner' }
Invoke-HerdrRuntime -RuntimeArgs $runtimeArgs
exit $script:HerdrRuntimeExitCode
