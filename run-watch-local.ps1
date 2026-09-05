#Requires -Version 5.1
param(
    [switch]$RegisterTask,
    [switch]$UnregisterTask,
    [switch]$Pause
)

$ErrorActionPreference = 'Continue'
$Root = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$TaskName = 'LINE Gift Coupon Watch Hourly'
$ScriptPath = Join-Path $Root 'run-watch-local.ps1'
$LogFile = Join-Path $Root 'coupon-watch.log'
$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
$Python = if ($PythonCmd) { $PythonCmd.Source } else { 'C:\Python313\python.exe' }

function Initialize-Utf8Console {
    try {
        chcp 65001 | Out-Null
        [Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
        $global:OutputEncoding = [Console]::OutputEncoding
    } catch {}
}

function Import-DotEnvLocal {
    $envFile = Join-Path $Root '.env.local'
    if (-not (Test-Path $envFile)) {
        Write-Warning ".env.local missing: $envFile"
        return
    }
    Get-Content -Path $envFile -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) { return }
        $key = $line.Substring(0, $eq).Trim()
        $val = $line.Substring($eq + 1).Trim()
        if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        Set-Item -Path ("Env:" + $key) -Value $val
    }
}

function Write-RunLog {
    param([string]$Message)
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Output $line
    [System.IO.File]::AppendAllText($LogFile, $line + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding $false))
}

if ($UnregisterTask) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output ("Removed task: " + $TaskName)
    exit 0
}

if ($RegisterTask) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    # Hidden: no console flash / mojibake window for hourly runs
    $arg = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $ScriptPath + '"'
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg -WorkingDirectory $Root
    $start = (Get-Date).Date.AddHours((Get-Date).Hour).AddMinutes(5)
    if ($start -le (Get-Date)) { $start = $start.AddHours(1) }
    $trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Hourly LINE gift coupon watch + Discord notify (local backup)' | Out-Null
    Write-Output ("Registered task: " + $TaskName + " (Hidden window)")
    Write-Output ("Next run approx: " + $start.ToString('yyyy-MM-dd HH:mm:ss'))
    Get-ScheduledTaskInfo -TaskName $TaskName | Format-List LastRunTime, NextRunTime, LastTaskResult
    exit 0
}

Initialize-Utf8Console
Import-DotEnvLocal
if (-not $env:ALWAYS_NOTIFY) { $env:ALWAYS_NOTIFY = '1' }
if (-not $env:SCAN_HOME) { $env:SCAN_HOME = '1' }
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

Set-Location $Root
Write-RunLog 'start local coupon watch'

# Capture Python output as UTF-8 bytes to avoid console cp950 mojibake in the log file
$outFile = Join-Path $Root '_watch_stdout.tmp'
$errFile = Join-Path $Root '_watch_stderr.tmp'
Remove-Item $outFile, $errFile -ErrorAction SilentlyContinue
$p = Start-Process -FilePath $Python `
    -ArgumentList @(Join-Path $Root 'watch_coupons.py') `
    -WorkingDirectory $Root `
    -Wait -PassThru -NoNewWindow `
    -RedirectStandardOutput $outFile `
    -RedirectStandardError $errFile
$exit = $p.ExitCode
foreach ($f in @($outFile, $errFile)) {
    if (Test-Path $f) {
        $text = [System.IO.File]::ReadAllText($f, (New-Object System.Text.UTF8Encoding $false))
        if ($text) {
            [System.IO.File]::AppendAllText($LogFile, $text, (New-Object System.Text.UTF8Encoding $false))
            if (-not ($text.EndsWith("`n"))) {
                [System.IO.File]::AppendAllText($LogFile, [Environment]::NewLine, (New-Object System.Text.UTF8Encoding $false))
            }
            Write-Output $text
        }
        Remove-Item $f -ErrorAction SilentlyContinue
    }
}

Write-RunLog ('end exit=' + $exit)
$report = Join-Path $Root 'latest-coupons.txt'
if (Test-Path $report) {
    Write-Output ''
    Write-Output '--- latest-coupons.txt ---'
    Write-Output ([System.IO.File]::ReadAllText($report, (New-Object System.Text.UTF8Encoding $false)))
}

if ($Pause) {
    Write-Output ''
    Write-Output 'Press Enter to close...'
    [void][System.Console]::ReadLine()
}

exit $exit
