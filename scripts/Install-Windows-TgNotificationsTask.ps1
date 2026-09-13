#requires -Version 7.6
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$StartNow,
    [switch]$NoStart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $PSBoundParameters.ContainsKey('StartNow')) {
    $StartNow = -not $NoStart
}

$taskName = 'TelegramNotifications'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\pythonw.exe'
$environmentFile = Join-Path $projectDirectory '.env'
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not $IsWindows) {
    throw 'This installer requires Windows Task Scheduler.'
}
if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw "Windowless virtual-environment Python was not found: $pythonExecutable"
}
if (-not (Test-Path -LiteralPath $environmentFile -PathType Leaf)) {
    throw "Configuration file was not found: $environmentFile"
}

$action = New-ScheduledTaskAction `
    -Execute $pythonExecutable `
    -Argument '-m tg_notification run' `
    -WorkingDirectory $projectDirectory
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable
$description = 'Runs the Telegram notification monitor at user logon. It uses the current user''s Codex authentication and restarts after failures.'

if ($PSCmdlet.ShouldProcess("scheduled task '$taskName'", 'register or update')) {
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description $description `
        -Force | Out-Null
}

if ($StartNow -and -not $WhatIfPreference) {
    $findMonitorProcesses = {
        Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
            Where-Object { $_.CommandLine -like '*-m tg_notification run*' }
    }

    $installedTask = Get-ScheduledTask -TaskName $taskName
    if ($installedTask.State -eq 'Running') {
        if ($PSCmdlet.ShouldProcess("scheduled task '$taskName'", 'stop running instance')) {
            Stop-ScheduledTask -TaskName $taskName
        }
    }

    $monitorProcesses = @(& $findMonitorProcesses)
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ($monitorProcesses.Count -gt 0 -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 250
        $monitorProcesses = @(& $findMonitorProcesses)
    }

    if ($monitorProcesses.Count -gt 0) {
        if ($PSCmdlet.ShouldProcess(
                ($monitorProcesses.ProcessId -join ', '),
                'force-stop leftover notification monitor processes'
            )) {
            foreach ($process in $monitorProcesses) {
                Stop-Process -Id $process.ProcessId -Force
                Wait-Process -Id $process.ProcessId -ErrorAction SilentlyContinue -Timeout 10
            }
        }
    }

    if ($PSCmdlet.ShouldProcess("scheduled task '$taskName'", 'start')) {
        Start-ScheduledTask -TaskName $taskName
    }
}

if ($WhatIfPreference) {
    return
}

Write-Output "Installed per-user autostart task: $taskName"
Write-Output "User: $currentUser"
Write-Output "Project: $projectDirectory"
if ($StartNow) {
    Write-Output 'The notification monitor was restarted.'
}
