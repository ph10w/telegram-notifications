#requires -Version 7.6
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$StartNow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

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
    -Argument '-m telegram_notifications run' `
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
    $installedTask = Get-ScheduledTask -TaskName $taskName
    if ($installedTask.State -eq 'Running') {
        if ($PSCmdlet.ShouldProcess("scheduled task '$taskName'", 'stop running instance')) {
            Stop-ScheduledTask -TaskName $taskName
            $deadline = [DateTime]::UtcNow.AddSeconds(30)
            do {
                Start-Sleep -Milliseconds 250
                $installedTask = Get-ScheduledTask -TaskName $taskName
            } while ($installedTask.State -eq 'Running' -and [DateTime]::UtcNow -lt $deadline)

            if ($installedTask.State -eq 'Running') {
                throw "Scheduled task '$taskName' did not stop within 30 seconds."
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
