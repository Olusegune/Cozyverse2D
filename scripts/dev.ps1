# Starts the worker (fixed dev port/token) and the Vite dev server for browser-based UI work.
# Dev-only token; the packaged app uses a random per-session token from the Tauri host.
param([int]$WorkerPort = 8765, [string]$Token = "dev-token-local-only-0001")
$root = Split-Path -Parent $PSScriptRoot
"VITE_WORKER_URL=http://127.0.0.1:$WorkerPort`nVITE_WORKER_TOKEN=$Token" | Set-Content -Encoding ascii "$root\apps\desktop\.env.local"
New-Item -ItemType Directory -Force "$root\evidence\devdata" | Out-Null
$env:CV_WORKER_TOKEN = $Token
Start-Process -FilePath "$root\.venv\Scripts\python.exe" -ArgumentList "-m", "cozyverse_worker", "--port", $WorkerPort, "--data-dir", "$root\evidence\devdata" `
  -WorkingDirectory "$root\worker" -WindowStyle Hidden -RedirectStandardOutput "$root\evidence\worker.out.log" -RedirectStandardError "$root\evidence\worker.err.log"
Start-Process -FilePath cmd.exe -ArgumentList "/c", "npm run dev" -WorkingDirectory "$root\apps\desktop" -WindowStyle Hidden `
  -RedirectStandardOutput "$root\evidence\vite.out.log" -RedirectStandardError "$root\evidence\vite.err.log"
Start-Sleep 5
"worker: http://127.0.0.1:$WorkerPort   ui: http://127.0.0.1:1420"
