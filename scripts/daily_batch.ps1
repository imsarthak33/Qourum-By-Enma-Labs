# Quorum daily outcome-accumulation pass (growth plan Horizon 0).
# Runs `quorum batch` (analyze the default basket, then resolve open verdicts)
# and appends output to logs\batch_YYYYMMDD.log. Registered in Windows Task
# Scheduler as "Quorum Daily Batch" (daily 08:30, catches up after missed
# starts). Re-register after moving the repo:
#   powershell -ExecutionPolicy Bypass -File scripts\register_daily_batch.ps1
$repo = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $repo "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("batch_{0:yyyyMMdd}.log" -f (Get-Date))

Set-Location $repo
$env:PYTHONIOENCODING = "utf-8"
"=== quorum batch started $(Get-Date -Format s) ===" | Out-File -Append -Encoding utf8 $log
# cmd-level redirection appends python's utf-8 bytes verbatim; PowerShell's
# own *>> re-encodes native output as utf-16 and garbles the log.
cmd /c "python -m quorum.cli batch >> `"$log`" 2>&1"
$code = $LASTEXITCODE
"=== finished $(Get-Date -Format s) exit=$code ===" | Out-File -Append -Encoding utf8 $log
exit $code
