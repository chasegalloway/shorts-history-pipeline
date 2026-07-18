# Registers a twice-daily Task Scheduler job for the shorts pipeline.
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$action = New-ScheduledTaskAction -Execute $py -Argument "-m pipeline.run --count 1" -WorkingDirectory $root
$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At 10:00),
    (New-ScheduledTaskTrigger -Daily -At 13:30),
    (New-ScheduledTaskTrigger -Daily -At 17:00)
)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "FacelessShortsPipeline" -Action $action -Trigger $triggers -Settings $settings -Force
Write-Host "Registered task 'FacelessShortsPipeline' (10:00, 13:30, 17:00 daily)."
