$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"
$clientPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (!(Test-Path $clientPython)) { $clientPython = "python" }
& $clientPython -X utf8 (Join-Path $PSScriptRoot "arena_client.py") @args
exit $LASTEXITCODE
