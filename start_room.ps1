$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"
$roomPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (!(Test-Path $roomPython)) { $roomPython = "python" }
& $roomPython -X utf8 (Join-Path $PSScriptRoot "arena_room.py") --open @args
exit $LASTEXITCODE
