<#
    run_viewer.ps1 - open Mail Filter as a desktop app window.

    Started automatically by the "MailFilterViewer" logon task (see
    install_autostart.ps1), and by the "Open Mail Filter" button on a
    reminder notification, via the mailfilter: protocol.

    What it does:
      * starts viewer.py with pythonw.exe, so there is no console window
        sitting in the taskbar for the rest of the session
      * waits for 127.0.0.1:8765 to answer before opening anything, so the
        window never lands on a connection-refused page at boot
      * opens the site in an Edge/Chrome --app window: no address bar, no
        tabs, its own taskbar button - a desktop app as far as anyone using
        it is concerned
      * if that window is already open, focuses it instead of making a second

    Usage:
        .\run_viewer.ps1              # start the server and open the window
        .\run_viewer.ps1 -NoWindow    # server only
        .\run_viewer.ps1 -Quiet       # no console output (used by the task)
#>

[CmdletBinding()]
param(
    [switch]$NoWindow,
    [switch]$Quiet,
    # The protocol handler appends the URL it was invoked with ("mailfilter:
    # open"). Swallow it rather than letting PowerShell error on it.
    [Parameter(ValueFromRemainingArguments = $true)]
    $Rest
)

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location -LiteralPath $ProjectDir

$Port = 8765
$Url  = "http://127.0.0.1:$Port/"
$LogFile = Join-Path $ProjectDir 'viewer.log'

function Write-Note([string]$Text) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Text
    if (-not $Quiet) { Write-Host $Text }
    try {
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::AppendAllText($LogFile, $line + "`r`n", $utf8)
    } catch { }
}

function Test-Port {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', $Port)
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

# -- the server --------------------------------------------------------------

if (Test-Port) {
    Write-Note "Viewer already running on $Url"
} else {
    $Pythonw = Join-Path $ProjectDir '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path -LiteralPath $Pythonw)) {
        $Pythonw = Join-Path $ProjectDir '.venv\Scripts\python.exe'
    }
    if (-not (Test-Path -LiteralPath $Pythonw)) {
        Write-Note "ERROR: virtualenv missing - recreate it with: py -3.12 -m venv .venv"
        exit 1
    }

    # viewer.py refuses to start with no digest to show. On a fresh install
    # that is normal: the 07:55 task will fetch one, and the next logon opens
    # the window. Say so in the log rather than popping an error at boot.
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir 'digest_store.json'))) {
        Write-Note "No digest yet - nothing to show. Skipping."
        exit 0
    }

    $env:PYTHONUTF8 = '1'
    Start-Process -FilePath $Pythonw `
                  -ArgumentList 'viewer.py', '--no-browser' `
                  -WorkingDirectory $ProjectDir `
                  -WindowStyle Hidden | Out-Null

    $up = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Port) { $up = $true; break }
    }
    if (-not $up) {
        Write-Note "ERROR: viewer did not come up on port $Port within 20s."
        exit 1
    }
    Write-Note "Viewer started on $Url"
}

if ($NoWindow) { exit 0 }

# -- the window --------------------------------------------------------------

function Get-AppBrowser {
    $candidates = @(
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    foreach ($path in $candidates) {
        if ($path -and (Test-Path -LiteralPath $path)) { return $path }
    }
    foreach ($exe in @('msedge.exe', 'chrome.exe')) {
        $key = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\$exe"
        try {
            $found = (Get-ItemProperty -LiteralPath $key -ErrorAction Stop).'(default)'
            if ($found -and (Test-Path -LiteralPath $found)) { return $found }
        } catch { }
    }
    return $null
}

# Already open? Bring it forward instead of stacking up a second window -
# the logon task and the notification button both land here.
$existing = $null
try {
    $existing = Get-CimInstance Win32_Process -Filter "Name='msedge.exe' OR Name='chrome.exe'" -ErrorAction Stop |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*--app=$Url*" } |
        Select-Object -First 1
} catch { }

if ($existing) {
    Write-Note "Window already open - focusing it."
    try {
        (New-Object -ComObject WScript.Shell).AppActivate([int]$existing.ProcessId) | Out-Null
    } catch { }
    exit 0
}

$Browser = Get-AppBrowser
if ($Browser) {
    Start-Process -FilePath $Browser -ArgumentList @(
        "--app=$Url",
        '--window-size=1180,860',
        '--window-position=90,60'
    ) | Out-Null
    Write-Note "Opened app window with $(Split-Path -Leaf $Browser)."
} else {
    # No Chromium browser anywhere: a normal browser tab still beats nothing.
    Start-Process $Url | Out-Null
    Write-Note "No Edge/Chrome found - opened $Url in the default browser."
}
exit 0
