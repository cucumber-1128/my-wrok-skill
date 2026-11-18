# Natural-language intake. This host is the Router; never launch a second terminal to receive an order.
param(
    [string]$Text,
    [string[]]$File,
    [ValidateSet('auto','story','bug')][string]$Kind,
    [string]$Sheet,
    [int]$FirstRow,
    [int]$LastRow,
    [ValidateSet('delivery','delivery-swapped')][string]$WorkflowProfile,
    [switch]$Preview,
    [string]$StateDir,
    [string]$Batch,
    [string]$Item,
    [switch]$Claim,
    [ValidateSet('CLAIMED','BLOCKED','HANDOFF','FAILED')][string]$Status,
    [string]$ClaimToken,
    [string]$ThreadId,
    [switch]$Retry,
    [switch]$TakeOver,
    [string]$PriorClaim,
    [switch]$ConfirmedStopped,
    [string]$Note,
    [string[]]$Evidence
)
$PSNativeCommandUseErrorActionPreference = $false
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
$python = if ($env:HERDR_PYTHON) { $env:HERDR_PYTHON } else { 'python' }
$command = Get-Command $python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $command) {
    [Console]::Error.WriteLine('{"ok":false,"error":{"message":"Python 3.10+ required; set HERDR_PYTHON to its executable"}}')
    exit 1
}
$cliArgs = @()
$names = @{
    Text='text'; File='file'; Kind='kind'; Sheet='sheet'; FirstRow='first-row'; LastRow='last-row';
    WorkflowProfile='workflow-profile'; Preview='preview'; StateDir='state-dir'; Batch='batch';
    Item='item'; Claim='claim'; Status='status'; ClaimToken='claim-token'; ThreadId='thread-id';
    Retry='retry'; TakeOver='takeover'; PriorClaim='prior-claim'; ConfirmedStopped='confirmed-stopped';
    Note='note'; Evidence='evidence'
}
foreach ($arg in $PSBoundParameters.GetEnumerator()) {
    if (-not $names.ContainsKey($arg.Key)) { continue }
    $flag = '--' + $names[$arg.Key]
    if ($arg.Value -is [System.Management.Automation.SwitchParameter]) {
        if ($arg.Value) { $cliArgs += $flag }
    } else {
        foreach ($value in @($arg.Value)) {
            # The equals form keeps arbitrary user text (even a leading "--") as data.
            $cliArgs += ($flag + '=' + [string]$value)
        }
    }
}
& $command.Source -B (Join-Path $PSScriptRoot 'sa_activate.py') @cliArgs
exit $LASTEXITCODE
