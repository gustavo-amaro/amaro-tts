# Starts the server at http://localhost:8000 (runs setup on first use).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = ".venv-win\Scripts\python.exe"
if (-not (Test-Path $py)) { & "$PSScriptRoot\setup.ps1" }

& $py -m uvicorn app.server:app --port 8000 @args
exit $LASTEXITCODE
