<#
    notify.ps1 - one Windows notification that stays put.

    A normal toast (and the tray balloon this replaces) fades after a few
    seconds. Miss it and the reminder is gone. This one uses the "reminder"
    scenario, which Windows keeps on screen until the person either clicks a
    button or clicks the X - exactly like an alarm or a calendar reminder.

    Two rules that scenario comes with, do not break either:
      * a reminder toast MUST carry at least one <action>. Without one
        Windows silently downgrades it to an ordinary toast that fades.
      * it must be sent under an AppUserModelID that Windows knows about,
        otherwise nothing appears at all. install_autostart.ps1 registers
        "MailFilter.Digest"; this script re-registers it if it is missing, so
        running the script alone is enough.

    Usage:
        .\notify.ps1 -Title "Tomorrow" -Body "09:00  M3 quiz"

    Exit codes:  0 sticky toast shown | 2 fell back to a fading balloon
                 1 nothing could be shown
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Title,
    [string]$Body = '',
    [string]$AppId = 'MailFilter.Digest',
    [string]$AppName = 'Mail Filter',
    # Clicking the toast body, or its Open button, activates this. The
    # mailfilter: protocol is registered by install_autostart.ps1 and opens
    # the app window.
    [string]$Launch = 'mailfilter:open',
    [string]$ButtonText = 'Open Mail Filter'
)

$ErrorActionPreference = 'Stop'

function Register-AppId {
    # A toast is refused unless its AppUserModelID is registered. A key under
    # HKCU is all Windows needs, and it also gives the toast a real name and
    # an entry in Settings > Notifications instead of "Windows PowerShell".
    $key = "HKCU:\SOFTWARE\Classes\AppUserModelId\$AppId"
    if (-not (Test-Path -LiteralPath $key)) {
        New-Item -Path $key -Force | Out-Null
    }
    New-ItemProperty -LiteralPath $key -Name 'DisplayName' -Value $AppName `
                     -PropertyType String -Force | Out-Null
    New-ItemProperty -LiteralPath $key -Name 'ShowInSettings' -Value 1 `
                     -PropertyType DWord -Force | Out-Null
}

function Show-StickyToast {
    Register-AppId

    [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
    [void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime]

    $esc = { param($t) [System.Security.SecurityElement]::Escape($t) }

    # ToastGeneric shows the title plus a few body lines. Give each line its
    # own <text> so they stay on separate lines, and roll the overflow into
    # the last one rather than letting Windows cut it off mid-sentence.
    $lines = @()
    if ($Body) {
        $lines = @($Body -split "`r?`n" | Where-Object { $_.Trim() -ne '' })
    }
    $shown = @($lines | Select-Object -First 3)
    if ($lines.Count -gt $shown.Count) {
        $extra = $lines.Count - $shown.Count
        if ($shown.Count -gt 0) {
            $shown[$shown.Count - 1] += "  (+$extra more)"
        } else {
            $shown = @("$extra more")
        }
    }

    $texts = "<text>" + (& $esc $Title) + "</text>"
    foreach ($line in $shown) {
        $texts += "<text>" + (& $esc $line) + "</text>"
    }

    $launchEsc = & $esc $Launch
    $buttonEsc = & $esc $ButtonText

    # scenario="reminder" is the whole point: it pins the toast on screen
    # until the person acts on it.
    $xml = @"
<toast scenario="reminder" activationType="protocol" launch="$launchEsc">
  <visual>
    <binding template="ToastGeneric">
      $texts
    </binding>
  </visual>
  <actions>
    <action content="$buttonEsc" activationType="protocol" arguments="$launchEsc"/>
    <action content="Dismiss" activationType="system" arguments="dismiss"/>
  </actions>
  <audio src="ms-winsoundevent:Notification.Reminder"/>
</toast>
"@

    $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
    $doc.LoadXml($xml)
    $toast = New-Object Windows.UI.Notifications.ToastNotification $doc
    # Keep it in the Action Center for a day if it is dismissed from screen.
    $toast.ExpirationTime = [DateTimeOffset]::Now.AddDays(1)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId).Show($toast)
}

function Show-Balloon {
    # Last resort for a machine where the toast platform is unavailable.
    # This one does fade - there is no way around that with a tray balloon.
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $icon = New-Object System.Windows.Forms.NotifyIcon
    try {
        $icon.Icon = [System.Drawing.SystemIcons]::Information
        $icon.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
        $icon.BalloonTipTitle = $Title
        $icon.BalloonTipText = $Body
        $icon.Visible = $true
        $icon.ShowBalloonTip(30000)
        Start-Sleep -Seconds 12
    } finally {
        $icon.Dispose()
    }
}

try {
    Show-StickyToast
    exit 0
} catch {
    Write-Error "Sticky toast failed: $($_.Exception.Message)" -ErrorAction Continue
    try {
        Show-Balloon
        exit 2
    } catch {
        Write-Error "Balloon failed too: $($_.Exception.Message)" -ErrorAction Continue
        exit 1
    }
}
