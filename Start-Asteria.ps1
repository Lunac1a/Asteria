param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$asteriaRoot = $PSScriptRoot
$asteriaPython = Join-Path $asteriaRoot 'backend/.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $asteriaPython) -or !(Test-Path -LiteralPath (Join-Path $asteriaRoot 'backend/.env'))) {
    throw 'Current trial dependencies/configuration missing. Read README.md; do not regenerate existing encryption keys.'
}
if ($CheckOnly) {
    & $asteriaPython (Join-Path $asteriaRoot 'backend/app/cli/check_trial.py')
    exit $LASTEXITCODE
}
$asteriaLogDir = Join-Path $asteriaRoot 'data/trial-launch'
New-Item -ItemType Directory -Force -Path $asteriaLogDir | Out-Null
$asteriaDb = docker ps --filter 'name=^/asteria-local-mvp-db$' --format '{{.Names}}'
if ($asteriaDb -ne 'asteria-local-mvp-db') {
    docker start asteria-local-mvp-db | Out-Null
    if ($LASTEXITCODE -ne 0) {throw 'Start Docker Desktop and the existing asteria-local-mvp-db container. No replacement database was created.'}
}
$asteriaStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$asteriaProcessFile = Join-Path $asteriaLogDir 'processes.json'
$asteriaProcesses = @{}
if (Test-Path -LiteralPath $asteriaProcessFile) {
    $asteriaSaved = Get-Content -LiteralPath $asteriaProcessFile -Raw | ConvertFrom-Json
    foreach ($asteriaProperty in $asteriaSaved.PSObject.Properties) { $asteriaProcesses[$asteriaProperty.Name] = $asteriaProperty.Value }
}
function Save-AsteriaProcess($Role, $Process, $Script) {
    $asteriaProcesses[$Role] = @{ id=$Process.Id; started=$Process.StartTime.ToUniversalTime().ToString('o'); script=$Script }
    $asteriaProcesses | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $asteriaProcessFile -Encoding utf8
}
if (!(Get-NetTCPConnection -LocalPort 18001 -State Listen -ErrorAction SilentlyContinue)) {
    $asteriaApiScript = Join-Path $asteriaRoot 'backend/app/cli/local.py'
    $asteriaApiProcess = Start-Process -FilePath $asteriaPython -ArgumentList @(('"'+$asteriaApiScript+'"'),'serve','--env-file','.env') -WorkingDirectory (Join-Path $asteriaRoot 'backend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $asteriaLogDir "$asteriaStamp-api.out.log") -RedirectStandardError (Join-Path $asteriaLogDir "$asteriaStamp-api.err.log") -PassThru
    Save-AsteriaProcess 'api' $asteriaApiProcess $asteriaApiScript
}
if (!(Get-NetTCPConnection -LocalPort 3001 -State Listen -ErrorAction SilentlyContinue)) {
    if (!(Test-Path -LiteralPath (Join-Path $asteriaRoot 'frontend/.next/BUILD_ID'))) {throw 'Build frontend first: cd frontend; npm run build. Backend is available.'}
    $asteriaNode = (Get-Command node.exe).Source
    $asteriaWebScript = Join-Path $asteriaRoot 'frontend/node_modules/next/dist/bin/next'
    $asteriaWebProcess = Start-Process -FilePath $asteriaNode -ArgumentList @(('"'+$asteriaWebScript+'"'),'start','--hostname','127.0.0.1','--port','3001') -WorkingDirectory (Join-Path $asteriaRoot 'frontend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $asteriaLogDir "$asteriaStamp-web.out.log") -RedirectStandardError (Join-Path $asteriaLogDir "$asteriaStamp-web.err.log") -PassThru
    Save-AsteriaProcess 'web' $asteriaWebProcess $asteriaWebScript
}
Write-Host 'Asteria: http://localhost:3001 . Existing listeners were preserved. Run .\Start-Asteria.ps1 -CheckOnly to verify readiness.'
