# Registers the Task Scheduler job for the shorts pipeline: 6 runs/day so there
# are 6 videos to fill the 6 publish slots (3 night + 3 day) in config.yaml.
# Each run produces 1 video, which upload.next_publish_slot schedules into the
# next free slot >=2h out.
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$action = New-ScheduledTaskAction -Execute $py -Argument "-m pipeline.run --count 1" -WorkingDirectory $root
$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At 23:00),
    (New-ScheduledTaskTrigger -Daily -At 02:00),
    (New-ScheduledTaskTrigger -Daily -At 05:00),
    (New-ScheduledTaskTrigger -Daily -At 10:00),
    (New-ScheduledTaskTrigger -Daily -At 14:00),
    (New-ScheduledTaskTrigger -Daily -At 18:00)
)
# WakeToRun lets the overnight runs pull the PC out of sleep
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "FacelessShortsPipeline" -Action $action -Trigger $triggers -Settings $settings -Force
Write-Host "Registered task 'FacelessShortsPipeline' (6 runs/day: 23,02,05,10,14,18 daily)."
