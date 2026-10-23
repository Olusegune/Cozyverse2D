# Builds runtime\python: a self-contained Python for the worker, so the installed app needs no Python install.
# It copies the interpreter this repo's .venv was created from, plus the .venv packages (minus test tooling).
# Nothing is downloaded. Usage:  .\scripts\build_runtime.ps1 [-Force] [-PythonHome <dir>]
param([switch]$Force, [string]$PythonHome)
$ErrorActionPreference = "Continue"   # native tools print progress on stderr; failures are caught through exit codes below
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) { throw "Create the .venv first (see README): $venv" }
if (-not $PythonHome) {
  $cfg = Get-Content "$venv\pyvenv.cfg" | Where-Object { $_ -match "^home\s*=" } | Select-Object -First 1
  $PythonHome = ($cfg -split "=", 2)[1].Trim()
}
if (-not (Test-Path "$PythonHome\python.exe")) { throw "Python home not found: $PythonHome" }
$out = Join-Path $root "runtime\python"
if (Test-Path $out) {
  if (-not $Force) { Write-Host "runtime\python already exists (use -Force to rebuild)"; return }
  Remove-Item -Recurse -Force $out
}
New-Item -ItemType Directory -Force $out | Out-Null

function Robo($src, $dst, [string[]]$extra) {
  & robocopy $src $dst /E /NFL /NDL /NJH /NJS /NP @extra | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE): $src" }
}

Write-Host "Interpreter from $PythonHome"
Copy-Item "$PythonHome\python.exe", "$PythonHome\pythonw.exe" $out
Get-ChildItem $PythonHome -File | Where-Object { $_.Name -match "^(python3\d*\.dll|python3\.dll|vcruntime.*\.dll|msvcp.*\.dll)$" } | Copy-Item -Destination $out
Robo "$PythonHome\DLLs" "$out\DLLs" @("/XF", "_tkinter.pyd", "tcl*.dll", "tk*.dll", "*.lib")
Robo "$PythonHome\Lib" "$out\Lib" @("/XD", "site-packages", "test", "tests", "idlelib", "tkinter", "turtledemo", "lib2to3", "ensurepip", "__pycache__", "/XF", "*.pyc")

Write-Host "Packages from $venv"
Robo "$venv\Lib\site-packages" "$out\Lib\site-packages" @("/XD", "pytest", "_pytest", "pluggy", "iniconfig", "pip", "__pycache__", "pytest-*", "pluggy-*", "iniconfig-*", "pip-*", "/XF", "*.pyc", "distutils-precedence.pth")

# smoke test: the bundled interpreter must import everything the worker needs, without the venv or user site
$env:PYTHONPATH = ""
& "$out\python.exe" -s -c "import fastapi, uvicorn, pydantic, PIL, numpy, jsonschema, httpx, keyring, sqlite3, ssl; print('runtime ok', __import__('sys').version.split()[0])"
if ($LASTEXITCODE -ne 0) { throw "runtime smoke test failed" }
$mb = [math]::Round(((Get-ChildItem $out -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
Write-Host "runtime\python ready ($mb MB)"
