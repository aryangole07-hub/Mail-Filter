<#
    run_alert.ps1 - wrapper used by the "MailFilterAlert" scheduled task.

    Exists so the task command is a single quoted path with no nested quotes;
    passing "python.exe" and "alerts.py" directly to schtasks /TR breaks as
    soon as the install path contains a space.

    Run it by hand to see tomorrow's agenda:  .\run_alert.ps1 --print
#>

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location -LiteralPath $ProjectDir

$Python = Join-Path $ProjectDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { exit 1 }

$env:PYTHONUTF8 = '1'
& $Python alerts.py @args
exit $LASTEXITCODE
