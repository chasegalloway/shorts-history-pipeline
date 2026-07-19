# Registers a twice-daily Task Scheduler job for the shorts pipeline.
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$action = New-ScheduledTaskAction -Execute $py -Argument "-m pipeline.run --count 1" -WorkingDirectory $root
$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At 23:00),
    (New-ScheduledTaskTrigger -Daily -At 01:00),
    (New-ScheduledTaskTrigger -Daily -At 03:00),
    (New-ScheduledTaskTrigger -Daily -At 05:00),
    (New-ScheduledTaskTrigger -Daily -At 18:00)
)
# WakeToRun lets the overnight runs pull the PC out of sleep
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "FacelessShortsPipeline" -Action $action -Trigger $triggers -Settings $settings -Force
Write-Host "Registered task 'FacelessShortsPipeline' (23:00, 01:00, 03:00, 05:00, 18:00 daily)."
