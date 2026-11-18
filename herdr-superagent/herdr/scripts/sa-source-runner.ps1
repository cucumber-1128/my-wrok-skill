param([Parameter(Mandatory = $true)][string]$CommandFile)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$global:LASTEXITCODE = 0
try {
    $sourceCommand = Get-Content -LiteralPath $CommandFile -Raw -Encoding utf8
    & ([scriptblock]::Create($sourceCommand))
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
