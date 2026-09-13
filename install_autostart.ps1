<#
    install_autostart.ps1 - make Mail Filter open itself when the PC starts,
    and make its reminders stay on screen.

    Run once (no admin needed):

        powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1

    It sets up four things, all under the current user only:

      1. "MailFilterViewer" - a scheduled task that fires 30 seconds after
         you log in and runs run_viewer.ps1, which starts the local server
         and opens the app window.
      2. "MailFilter.Digest" - the notification identity, so reminders are
         labelled "Mail Filter" and appear under that name in
         Settings > System > Notifications.
      3. mailfilter: - a URL protocol, so the "Open Mail Filter" button on a
         notification opens the app window.
      4. A Start-menu shortcut, for opening it by hand.

    Undo all of it with:

        powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1 -Uninstall
#>

[CmdletBinding()]
param(
    [switch]$Uninstall,
    # Seconds to wait after logon before opening the window. Long enough for
    # the desktop to settle, short enough that it is there when you look.
    [int]$DelaySeconds = 30
)

$ErrorActionPreference = 'Stop'

$ProjectDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ViewerTask  = 'MailFilterViewer'
$AppId       = 'MailFilter.Digest'
$AppName     = 'Mail Filter'
$Protocol    = 'mailfilter'
$RunViewer   = Join-Path $ProjectDir 'run_viewer.ps1'
$PowerShell  = Join-Path $PSHOME 'powershell.exe'
$StartMenu   = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Mail Filter.lnk'

function Say([string]$Text) { Write-Host "  $Text" }

# ---------------------------------------------------------------- uninstall

if ($Uninstall) {
    Write-Host "Removing Mail Filter autostart..." -ForegroundColor Cyan

    try {
        Unregister-ScheduledTask -TaskName $ViewerTask -Confirm:$false -ErrorAction Stop
        Say "Logon task removed."
    } catch { Say "No logon task to remove." }

    foreach ($key in @("HKCU:\SOFTWARE\Classes\AppUserModelId\$AppId",
                       "HKCU:\SOFTWARE\Classes\$Protocol")) {
        if (Test-Path -LiteralPath $key) {
            Remove-Item -LiteralPath $key -Recurse -Force
            Say "Removed $key"
        }
    }

    if (Test-Path -LiteralPath $StartMenu) {
        Remove-Item -LiteralPath $StartMenu -Force
        Say "Start-menu shortcut removed."
    }

    Write-Host "Done. The digest and reminder tasks are untouched." -ForegroundColor Green
    exit 0
}

# ------------------------------------------------------------------ install

Write-Host "Setting up Mail Filter autostart..." -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $RunViewer)) {
    Write-Host "ERROR: run_viewer.ps1 is missing from $ProjectDir" -ForegroundColor Red
    exit 1
}

# 1. open at logon ------------------------------------------------------------
$arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Quiet' -f $RunViewer

try {
    $action = New-ScheduledTaskAction -Execute $PowerShell -Argument $arguments `
                                      -WorkingDirectory $ProjectDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    # A window that fights the rest of the logon stampede loses. Let it settle.
    $trigger.Delay = "PT{0}S" -f $DelaySeconds
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                                             -DontStopIfGoingOnBatteries `
                                             -Hidden `
                                             -ExecutionTimeLimit ([TimeSpan]::FromMinutes(10))
    # Interactive, not "run whether logged on or not": a window can only be
    # drawn inside a real desktop session.
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
                                            -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $ViewerTask -Action $action -Trigger $trigger `
                           -Settings $settings -Principal $principal -Force `
                           -Description 'Opens the Mail Filter app window after logon.' | Out-Null
    Say "Logon task '$ViewerTask' created (opens ${DelaySeconds}s after you sign in)."
} catch {
    Write-Host "  Could not create the logon task: $($_.Exception.Message)" -ForegroundColor Yellow
    Say "You can still open Mail Filter from the Start menu."
}

# 2. notification identity ----------------------------------------------------
$appKey = "HKCU:\SOFTWARE\Classes\AppUserModelId\$AppId"
New-Item -Path $appKey -Force | Out-Null
New-ItemProperty -LiteralPath $appKey -Name 'DisplayName' -Value $AppName `
                 -PropertyType String -Force | Out-Null
New-ItemProperty -LiteralPath $appKey -Name 'ShowInSettings' -Value 1 `
                 -PropertyType DWord -Force | Out-Null
Say "Notifications will be labelled '$AppName'."

# 3. mailfilter: protocol -----------------------------------------------------
$protoKey = "HKCU:\SOFTWARE\Classes\$Protocol"
New-Item -Path $protoKey -Force | Out-Null
New-ItemProperty -LiteralPath $protoKey -Name '(default)' -Value "URL:$AppName" `
                 -PropertyType String -Force | Out-Null
New-ItemProperty -LiteralPath $protoKey -Name 'URL Protocol' -Value '' `
                 -PropertyType String -Force | Out-Null
$cmdKey = "$protoKey\shell\open\command"
New-Item -Path $cmdKey -Force | Out-Null
# %1 is the whole mailfilter:... URL; run_viewer.ps1 ignores it.
$command = '"{0}" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{1}" -Quiet "%1"' `
           -f $PowerShell, $RunViewer
New-ItemProperty -LiteralPath $cmdKey -Name '(default)' -Value $command `
                 -PropertyType String -Force | Out-Null
Say "'Open Mail Filter' on a notification now opens the app window."

# 4. Start-menu shortcut ------------------------------------------------------
try {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($StartMenu)
    $link.TargetPath = $PowerShell
    $link.Arguments = $arguments
    $link.WorkingDirectory = $ProjectDir
    $link.Description = 'Open Mail Filter'
    $link.WindowStyle = 7   # minimised: the console is a launcher, not the app
    $link.Save()
    Say "Start-menu shortcut created."
} catch {
    Write-Host "  Could not create the Start-menu shortcut: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done. Mail Filter will open by itself next time you start the PC." -ForegroundColor Green
Write-Host "Open it now with:  .\run_viewer.ps1" -ForegroundColor DarkGray
