# start.ps1 — fallback launcher for running outside VS Code
# If using VS Code, run the "Start email Agent" task instead:
#   Ctrl+Shift+B  (default build task)
#   or: Terminal > Run Task > Start email Agent

$root = $PSScriptRoot

Write-Host "Starting email Agent (separate windows)..." -ForegroundColor Cyan
Write-Host "Tip: inside VS Code, use Ctrl+Shift+B to run in integrated terminals." -ForegroundColor DarkGray
Write-Host ""

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$root\backend'; .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload"
)

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$root\frontend'; node proxy-server.mjs"
)

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$root\frontend'; npm run dev"
)

Write-Host "3 terminal windows launched:" -ForegroundColor Green
Write-Host "  Backend  -> http://127.0.0.1:8000  (docs: http://127.0.0.1:8000/docs)" -ForegroundColor Yellow
Write-Host "  Proxy    -> http://127.0.0.1:8001" -ForegroundColor Yellow
Write-Host "  Frontend -> http://localhost:5173" -ForegroundColor Yellow
