# All arguments are passed as an argv array. Run --help for the JSON state CLI.
. (Join-Path $PSScriptRoot 'sa-entry.ps1')
Invoke-HerdrRuntime -RuntimeArgs $args
exit $script:HerdrRuntimeExitCode
