# Runs the test suite (runs setup on first use).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = ".venv-win\Scripts\python.exe"
if (-not (Test-Path $py)) { & "$PSScriptRoot\setup.ps1" }

& $py -m pytest @args
exit $LASTEXITCODE
