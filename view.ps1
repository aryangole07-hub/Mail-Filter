<#
    view.ps1 - open the local mail viewer.

    Starts the little web server in viewer.py and opens it in your browser.
    It listens on 127.0.0.1 only, so nothing is reachable from the network.

    Usage:  .\view.ps1
    Stop it with Ctrl+C in this window.
#>

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location -LiteralPath $ProjectDir

$Python = Join-Path $ProjectDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "ERROR: virtualenv missing at $Python" -ForegroundColor Red
    Write-Host "Recreate it with:  py -3.12 -m venv .venv" -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir 'digest_store.json'))) {
    Write-Host "No digest yet - fetching one first..." -ForegroundColor Cyan
    & $Python mail_filter.py
}

$env:PYTHONUTF8 = '1'
& $Python viewer.py @args
