[CmdletBinding()]
param(
    [string]$ResearchExe,
    [string]$Database,
    [string]$Config,
    [string]$Markers,
    [datetime]$At = [datetime]::Today.AddHours(19),
    [string]$TaskName = 'Pread Weekly Recommendation'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
if (!$ResearchExe) { $ResearchExe = Join-Path $projectRoot '.venv\Scripts\research.exe' }
if (!$Database) { $Database = Join-Path $projectRoot 'data\research.db' }
$researchPath = (Resolve-Path -LiteralPath $ResearchExe).Path
$databasePath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Database)
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw "A scheduled task named '$TaskName' already exists."
}
$arguments = '--db "{0}"' -f $databasePath
if ($Config) {
    $arguments += ' --config "{0}"' -f (Resolve-Path -LiteralPath $Config).Path
}
$arguments += ' weekly'
if ($Markers) {
    $markersPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Markers)
    $arguments += ' --markers "{0}"' -f $markersPath
}
$action = New-ScheduledTaskAction -Execute $researchPath -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
$registration = @{
    TaskName = $TaskName
    Action = $action
    Trigger = $trigger
    Settings = $settings
    Description = 'Generate the local Pread paper recommendation for the current ISO week.'
}
Register-ScheduledTask @registration
