# One command to build the Windows installer (NSIS, per-user, no admin rights needed).
#   .\scripts\build_installer.ps1
# Prerequisites: Node 20+, Rust (MSVC), the repo .venv (see README). Blender is NOT bundled (see docs/adr/0002).
# The first build lets the Tauri CLI fetch its own NSIS toolchain into its cache.
param([switch]$SkipTests)
$ErrorActionPreference = "Continue"   # native tools print progress on stderr; failures are caught through exit codes below
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Need($name) { if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { throw "Missing prerequisite: $name" } }
Need node; Need npm; Need cargo
if (-not (Test-Path "$root\.venv\Scripts\python.exe")) { throw "Create the .venv first (see README)." }

if (-not $SkipTests) {
  Write-Host "== Fast tests (no Blender) =="
  & "$root\.venv\Scripts\python.exe" -m pytest -q -m "not blender" tests
  if ($LASTEXITCODE -ne 0) { throw "tests failed" }
}

Write-Host "== Python runtime =="
& "$root\scripts\build_runtime.ps1" -Force

Write-Host "== Clean bytecode caches so they are not bundled =="
Get-ChildItem "$root\worker", "$root\blender" -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force

Write-Host "== Front end + installer =="
Set-Location "$root\apps\desktop"
npm install --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
npx tauri build --bundles nsis
if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }

$setup = Get-ChildItem "$root\apps\desktop\src-tauri\target\release\bundle\nsis" -Filter "*-setup.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $setup) { throw "installer not found" }
New-Item -ItemType Directory -Force "$root\dist-installer" | Out-Null
Copy-Item $setup.FullName "$root\dist-installer\" -Force
$dest = Join-Path "$root\dist-installer" $setup.Name
$hash = (Get-FileHash $dest -Algorithm SHA256).Hash
"$hash  $($setup.Name)" | Set-Content "$dest.sha256" -Encoding ascii
Write-Host ("Installer: {0} ({1} MB)`nSHA256:    {2}" -f $dest, [math]::Round($setup.Length / 1MB, 1), $hash)
