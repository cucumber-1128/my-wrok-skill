function Invoke-HerdrRuntime {
    param([string[]]$RuntimeArgs)
    $PSNativeCommandUseErrorActionPreference = $false
    $script:HerdrRuntimeExitCode = 1
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    $OutputEncoding = [Text.UTF8Encoding]::new($false)
    $runtimePython = if ($env:HERDR_PYTHON) { $env:HERDR_PYTHON } else { 'python' }
    $runtimeCommand = Get-Command $runtimePython -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $runtimeCommand) {
        [Console]::Error.WriteLine('{"ok":false,"error":{"kind":"python_missing","message":"Python 3.10+ is required. Set HERDR_PYTHON to its executable."}}')
        $script:HerdrRuntimeExitCode = 1
        return
    }
    try {
        & $runtimeCommand.Source -B (Join-Path $PSScriptRoot 'sa.py') @RuntimeArgs
        $script:HerdrRuntimeExitCode = $LASTEXITCODE
    } catch {
        $runtimeError = @{ ok = $false; error = @{ kind = 'runtime_failed'; message = $_.Exception.Message } }
        [Console]::Error.WriteLine(($runtimeError | ConvertTo-Json -Compress))
    }
}
