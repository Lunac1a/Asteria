[CmdletBinding(SupportsShouldProcess)]
param([switch]$Database)
$ErrorActionPreference = 'Stop'
$asteriaProcessFile = Join-Path $PSScriptRoot 'data/trial-launch/processes.json'
if (Test-Path -LiteralPath $asteriaProcessFile) {
    $asteriaRegistry = Get-Content -LiteralPath $asteriaProcessFile -Raw | ConvertFrom-Json
    foreach ($asteriaRole in @('web','api')) {
        $asteriaEntry = $asteriaRegistry.$asteriaRole
        if (!$asteriaEntry) { continue }
        $asteriaRootProcess = Get-Process -Id $asteriaEntry.id -ErrorAction SilentlyContinue
        if (!$asteriaRootProcess) { continue }
        # Refuse a recycled PID or a process outside this checkout.
        if ($asteriaRootProcess.StartTime.ToUniversalTime().Ticks -ne ([datetime]$asteriaEntry.started).ToUniversalTime().Ticks) {
            throw "The recorded $asteriaRole PID was reused; no process was stopped."
        }
        $asteriaAll = @(Get-CimInstance Win32_Process)
        $asteriaCommand = ($asteriaAll | Where-Object ProcessId -eq $asteriaEntry.id).CommandLine
        $asteriaScript = [IO.Path]::GetFullPath($asteriaEntry.script)
        $asteriaExpected = if ($asteriaRole -eq 'api') { Join-Path $PSScriptRoot 'backend/app/cli/local.py' } else { Join-Path $PSScriptRoot 'frontend/node_modules/next/dist/bin/next' }
        if ($asteriaScript -ne [IO.Path]::GetFullPath($asteriaExpected) -or !$asteriaCommand.Contains($asteriaScript)) {
            throw "The recorded $asteriaRole process does not match this checkout; preserved."
        }
        $asteriaTargets = @($asteriaAll | Where-Object ProcessId -eq $asteriaEntry.id)
        $asteriaParentIds = @([int]$asteriaEntry.id)
        while ($asteriaParentIds.Count) {
            $asteriaChildren = @($asteriaAll | Where-Object { $_.ParentProcessId -in $asteriaParentIds })
            $asteriaTargets += $asteriaChildren
            $asteriaParentIds = @($asteriaChildren | ForEach-Object { [int]$_.ProcessId })
        }
        if ($PSCmdlet.ShouldProcess("Asteria $asteriaRole in $PSScriptRoot", 'Stop process tree')) {
            [array]::Reverse($asteriaTargets)
            foreach ($asteriaTarget in $asteriaTargets) {
                $asteriaNow = Get-CimInstance Win32_Process -Filter "ProcessId=$($asteriaTarget.ProcessId)"
                if ($asteriaNow -and $asteriaNow.CreationDate -eq $asteriaTarget.CreationDate) {
                    Stop-Process -Id $asteriaNow.ProcessId -ErrorAction SilentlyContinue
                }
            }
        }
    }
} else { Write-Host 'No script-owned process record. Use Ctrl+C in manually started terminals; unknown listeners are preserved.' }
if ($Database -and $PSCmdlet.ShouldProcess('asteria-local-mvp-db', 'Stop database container, keep data volume')) {
    docker stop asteria-local-mvp-db | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop database container.' }
}
Write-Host 'Asteria stop completed. Data and Provider gateway were preserved.'
