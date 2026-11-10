# Creates the Windows virtualenv (.venv-win) with PyTorch XPU (Intel Arc) and the project deps.
# The Linux .venv is left untouched.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$venv = ".venv-win"
$py = "$venv\Scripts\python.exe"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv not found. Install it from https://docs.astral.sh/uv/getting-started/installation/"
}

if (-not (Test-Path $py)) {
    uv venv $venv --python 3.12
    if ($LASTEXITCODE) { throw "failed to create $venv" }
}

# PyTorch with XPU support first, so voxcpm does not pull the CPU build.
uv pip install --python $py torch torchaudio --index-url https://download.pytorch.org/whl/xpu
if ($LASTEXITCODE) { throw "failed to install PyTorch XPU" }

uv pip install --python $py voxcpm fastapi uvicorn python-multipart pymupdf soundfile numpy -r requirements-dev.txt
if ($LASTEXITCODE) { throw "failed to install dependencies" }

& $py -c "import torch; print('torch', torch.__version__, '| XPU available:', torch.xpu.is_available())"
