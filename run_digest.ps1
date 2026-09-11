<#
    run_digest.ps1 - wrapper used by the "MailFilterDigest" scheduled task.

    Handles the things a bare "python mail_filter.py" gets wrong when nobody
    is at the keyboard:
      * runs from the project folder, using the venv's Python
      * forces UTF-8 so the digest emoji survive being written to a log file
      * marks the run non-interactive, so a stale Gmail token fails with a
        readable message instead of hanging on an invisible browser window
      * makes sure the local Ollama server is up, starting it if the machine
        booted without it (there is nobody around to start it by hand)
      * rotates digest.log so it cannot grow without bound

    You can also run it by hand:  .\run_digest.ps1 --hours 48
#>

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location -LiteralPath $ProjectDir

$Python  = Join-Path $ProjectDir '.venv\Scripts\python.exe'
$LogFile = Join-Path $ProjectDir 'digest.log'

if (-not (Test-Path -LiteralPath $Python)) {
    "ERROR: virtualenv missing at $Python - recreate it with: py -3.12 -m venv .venv" |
        Out-File -FilePath $LogFile -Append -Encoding utf8
    exit 1
}

# Keep one previous log around, capped at 5 MB.
if ((Test-Path -LiteralPath $LogFile) -and ((Get-Item -LiteralPath $LogFile).Length -gt 5MB)) {
    Move-Item -LiteralPath $LogFile -Destination "$LogFile.1" -Force
}

$env:PYTHONUTF8 = '1'
$env:MAIL_FILTER_NONINTERACTIVE = '1'

# Classification runs on a local Ollama model, so the server has to be up.
# On a machine that just booted, or where Ollama Desktop is not set to start
# with Windows, nobody is awake at 07:55 to start it - so start it here.
function Test-Ollama {
    try {
        Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' `
            -UseBasicParsing -TimeoutSec 5 | Out-Null
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-Ollama)) {
    $Ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    if ($Ollama) {
        Start-Process -FilePath $Ollama -ArgumentList 'serve' -WindowStyle Hidden
        # Loading is lazy, so this only waits for the socket, not the model.
        for ($i = 0; $i -lt 20; $i++) {
            Start-Sleep -Seconds 1
            if (Test-Ollama) { break }
        }
    }
}

# Append log lines as raw UTF-8 with no BOM. (PowerShell 5.1's Out-File and
# *>> redirection write UTF-16 here, which corrupts a log the Python side is
# writing as UTF-8.)
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Write-Log([string]$Text) {
    [System.IO.File]::AppendAllText($LogFile, $Text + "`r`n", $Utf8NoBom)
}

Write-Log ""
Write-Log "===== run at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====="

# Hand the redirection to cmd.exe rather than PowerShell: PS 5.1 wraps a
# native program's stderr in a NativeCommandError record, which would bury a
# one-line error under a page of PowerShell stack noise. Everything below is
# relative to $ProjectDir, so the path's space needs no quoting.
[Environment]::CurrentDirectory = $ProjectDir
$ArgString = ($args | ForEach-Object { $_ }) -join ' '
$Command = ".venv\Scripts\python.exe mail_filter.py $ArgString >> digest.log 2>&1"

& cmd.exe /c $Command
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Log "(mail_filter.py exited with code $code)"
}
exit $code
