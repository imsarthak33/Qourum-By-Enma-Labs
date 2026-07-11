# Registers (or re-registers) the "Quorum Daily Batch" scheduled task.
# 08:30 local time: after the US close and before the NSE open, so both
# markets have clean completed daily bars. StartWhenAvailable catches up
# if the machine was off at 08:30 - resolve is idempotent and walks price
# history forward from each verdict date, so missed days delay nothing.
$script = Join-Path $PSScriptRoot "daily_batch.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At 08:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "Quorum Daily Batch" -Action $action `
    -Trigger $trigger -Settings $settings -Force `
    -Description ("Quorum growth plan Horizon 0: daily `quorum batch` - " +
                  "analyze the symbol basket, resolve open verdicts, feed the leaderboard.")
