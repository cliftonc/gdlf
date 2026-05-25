"""PowerShell scripts + cmd wrappers bundled into the Windows enrolment zip.

Three scripts are generated per (kid, device) and shipped inside the .zip
alongside the WG MSI and per-kid .conf:

  Install.cmd   — self-elevating .cmd entry point the parent double-clicks.
                  Triggers a UAC prompt and then runs install.ps1 with
                  ExecutionPolicy Bypass.

  install.ps1   — runs once as Administrator (via the UAC-elevated .cmd).
                  Installs the CA, installs WG, registers the per-tunnel
                  service, drops reconcile.ps1, registers the SYSTEM
                  scheduled task, sets the LimitedOperatorUI registry
                  value, ACLs the install directory.

  reconcile.ps1 — runs from the scheduled task (boot + every 5 min) as
                  SYSTEM. Idempotent guard: re-asserts the service is
                  Running + Auto, the conf hash matches what was baked in,
                  and the LimitedOperatorUI value is set.

All three are baked at build time with the (kid, device, wg_ip, tunnel
name, conf hash, MDM CA fingerprint) substituted in via simple string
interpolation. They contain no secrets — the wg-quick .conf next to them
holds the device's WG private key.

The historical reason this is .zip + .cmd rather than a Windows
Provisioning Package (.ppkg): .ppkg is internally a Windows Imaging
Format (WIM) archive with a multi-XML compiled structure (including
per-setting SettingsGroup GUIDs that aren't publicly documented). Only
Microsoft's icd.exe / Windows Configuration Designer can produce a
compliant one. Building .ppkg from scratch in Python is not viable.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScriptContext:
    """Everything the on-device PowerShell scripts need to know about the
    enrolment they're applying."""
    kid_name: str
    device_name: str
    wg_ip: str
    tunnel_name: str        # used as both filename and service suffix
    conf_filename: str      # e.g. "gdlf-alice-laptop.conf" — inside ProgramData
    conf_sha256: str        # hex; reconcile.ps1 compares against on-disk hash
    ca_filename: str        # e.g. "gdlf-mitm-ca.crt"
    ca_sha1: str            # hex; certutil -delstore uses this thumbprint
    msi_filename: str       # e.g. "wireguard.msi"
    package_id: str         # GUID kept for state-tracking continuity
    # "Phone home" — install.ps1's last step POSTs a mark-enrolled call
    # to the dashboard so the parent doesn't have to click Mark applied
    # by hand. Both fields can be empty strings, in which case the script
    # skips the phone-home step entirely.
    dashboard_base_url: str
    shortlink_code: str


# ---------------------------------------------------------------------------
# Install.cmd — self-elevating wrapper the parent double-clicks. Triggers a
# UAC prompt then re-launches itself elevated; the elevated half invokes
# install.ps1 with the script directory as the working dir so the .ps1 can
# find the bundled MSI / CA / .conf via $PSScriptRoot.


INSTALL_CMD = r"""@echo off
REM gdlf Windows enrolment — self-elevating UAC wrapper.

REM Detect whether we're already elevated. `net session` requires admin
REM and returns errorlevel 0 when so. If not, relaunch via PowerShell
REM Start-Process -Verb RunAs (this is what shows the UAC prompt).
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting Administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set RC=%errorlevel%
echo.
if %RC% neq 0 (
    echo install.ps1 exited with code %RC%.
) else (
    echo gdlf enrolment complete. You can close this window.
)
pause
exit /b %RC%
"""


# ---------------------------------------------------------------------------
# install.ps1 — one-shot, runs elevated (via Install.cmd UAC wrapper).
#
# The script's working directory is its own folder (Install.cmd does
# `cd /d %~dp0` before invoking it), so bundled files are reachable via
# $PSScriptRoot.


APPLY_PS1 = r"""# gdlf Windows enrolment — Administrator-context install script.
# Runs once via Install.cmd's UAC wrapper. Idempotent: safe to re-run.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$gdlfDir = "C:\ProgramData\gdlf"
$kidName = "__KID_NAME__"
$deviceName = "__DEVICE_NAME__"
$wgIp = "__WG_IP__"
$tunnelName = "__TUNNEL_NAME__"
$confName = "__CONF_FILENAME__"
$confSha = "__CONF_SHA256__"
$caName = "__CA_FILENAME__"
$caSha1 = "__CA_SHA1__"
$msiName = "__MSI_FILENAME__"
$packageId = "__PACKAGE_ID__"
$dashboardBase = "__DASHBOARD_BASE_URL__"
$shortlink = "__SHORTLINK_CODE__"

# Bundled files live next to this script — Install.cmd `cd`s to the
# script's own folder before invoking us, but $PSScriptRoot is the
# authoritative reference regardless.
$assetDir = $PSScriptRoot
if (-not $assetDir -or -not (Test-Path $assetDir)) {
    $assetDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}

function Log($msg) {
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Write-Host "[$stamp] [gdlf-apply] $msg"
}

Log "starting enrolment for $kidName/$deviceName ($wgIp)"
Log "asset dir: $assetDir"

# 1. Make sure ProgramData\gdlf exists with ACLs locked to SYSTEM +
#    Administrators. Standard users (the kid) have no rights here.
if (-not (Test-Path $gdlfDir)) {
    New-Item -ItemType Directory -Path $gdlfDir | Out-Null
}
$acl = Get-Acl $gdlfDir
$acl.SetAccessRuleProtection($true, $false)
$acl.Access | ForEach-Object { $acl.RemoveAccessRule($_) | Out-Null }
$systemRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    "NT AUTHORITY\SYSTEM", "FullControl",
    "ContainerInherit,ObjectInherit", "None", "Allow")
$adminRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    "BUILTIN\Administrators", "FullControl",
    "ContainerInherit,ObjectInherit", "None", "Allow")
$acl.AddAccessRule($systemRule)
$acl.AddAccessRule($adminRule)
Set-Acl -Path $gdlfDir -AclObject $acl

# 2. Install the gdlf mitmproxy CA into LocalMachine\Root. MDM-pushed
#    CAs are system-trusted on Windows except Firefox, which uses its
#    own store (see docs/setup-windows-mdm.md gotcha).
$caPath = Join-Path $assetDir $caName
if (-not (Test-Path $caPath)) {
    throw "CA cert missing from package: $caPath"
}
Log "importing CA $caName into LocalMachine\Root"
$cert = Import-Certificate -FilePath $caPath -CertStoreLocation Cert:\LocalMachine\Root
Log "CA imported; thumbprint $($cert.Thumbprint)"

# 3. Install WireGuard MSI silently if not already present. WG installs
#    itself to %ProgramFiles%\WireGuard\ regardless of locale.
$wgExe = "$env:ProgramFiles\WireGuard\wireguard.exe"
if (-not (Test-Path $wgExe)) {
    $msiPath = Join-Path $assetDir $msiName
    if (-not (Test-Path $msiPath)) {
        throw "WireGuard MSI missing from package: $msiPath"
    }
    Log "installing $msiName silently"
    $proc = Start-Process msiexec.exe -ArgumentList "/i `"$msiPath`" /qn /norestart" -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "msiexec failed: exit code $($proc.ExitCode)"
    }
} else {
    Log "WireGuard already installed; skipping MSI"
}
if (-not (Test-Path $wgExe)) {
    throw "WireGuard install completed but wireguard.exe not found at $wgExe"
}

# 4. Drop the per-kid wg-quick conf into ProgramData\gdlf.
$confSrc = Join-Path $assetDir $confName
$confDst = Join-Path $gdlfDir $confName
if (-not (Test-Path $confSrc)) {
    throw "WireGuard conf missing from package: $confSrc"
}
Copy-Item -Path $confSrc -Destination $confDst -Force

# 5. Register the per-tunnel Windows service. WG's /installtunnelservice
#    is idempotent: re-running with the same conf updates in place.
Log "registering tunnel service WireGuardTunnel`$$tunnelName"
$proc = Start-Process -FilePath $wgExe `
    -ArgumentList "/installtunnelservice `"$confDst`"" `
    -Wait -PassThru -NoNewWindow
if ($proc.ExitCode -ne 0) {
    throw "wireguard.exe /installtunnelservice exited $($proc.ExitCode)"
}

# 6. Lock down the WireGuard tray UI for non-admins (hides Edit/Remove
#    buttons on Standard User sessions). The kid would still be denied
#    by the service ACL even without this; LimitedOperatorUI just stops
#    the buttons from showing.
New-Item -Path "HKLM:\Software\WireGuard" -Force | Out-Null
New-ItemProperty -Path "HKLM:\Software\WireGuard" -Name "LimitedOperatorUI" `
    -Value 1 -PropertyType DWord -Force | Out-Null

# 7. Drop the reconcile script and register a SYSTEM scheduled task that
#    re-asserts state every 5 minutes + on boot. This is gdlf's local
#    equivalent of the nftables/ sidecar — see CLAUDE.md.
$reconcileSrc = Join-Path $assetDir "reconcile.ps1"
$reconcileDst = Join-Path $gdlfDir "reconcile.ps1"
if (-not (Test-Path $reconcileSrc)) {
    throw "reconcile.ps1 missing from package"
}
Copy-Item -Path $reconcileSrc -Destination $reconcileDst -Force

$taskName = "gdlf-reconcile"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$reconcileDst`""
$triggerBoot = New-ScheduledTaskTrigger -AtStartup
$triggerEvery = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopOnIdleEnd -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
Register-ScheduledTask -TaskName $taskName -Force `
    -Action $action -Trigger @($triggerBoot, $triggerEvery) `
    -Settings $settings -Principal $principal | Out-Null

# 8. Persist enrolment metadata so future re-applies are idempotent and
#    the parent can read what's installed via `reg query`.
New-Item -Path "HKLM:\Software\gdlf" -Force | Out-Null
$enrollKey = "HKLM:\Software\gdlf\Enrollment"
New-Item -Path $enrollKey -Force | Out-Null
New-ItemProperty -Path $enrollKey -Name "Kid"           -Value $kidName    -PropertyType String -Force | Out-Null
New-ItemProperty -Path $enrollKey -Name "Device"        -Value $deviceName -PropertyType String -Force | Out-Null
New-ItemProperty -Path $enrollKey -Name "WgIp"          -Value $wgIp       -PropertyType String -Force | Out-Null
New-ItemProperty -Path $enrollKey -Name "TunnelName"    -Value $tunnelName -PropertyType String -Force | Out-Null
New-ItemProperty -Path $enrollKey -Name "ConfName"      -Value $confName   -PropertyType String -Force | Out-Null
New-ItemProperty -Path $enrollKey -Name "ConfSha256"    -Value $confSha    -PropertyType String -Force | Out-Null
New-ItemProperty -Path $enrollKey -Name "CaThumbprint"  -Value $caSha1     -PropertyType String -Force | Out-Null
New-ItemProperty -Path $enrollKey -Name "PackageId"     -Value $packageId  -PropertyType String -Force | Out-Null
New-ItemProperty -Path $enrollKey -Name "EnrolledAtUtc" -Value ((Get-Date).ToUniversalTime().ToString("o")) -PropertyType String -Force | Out-Null
if ($dashboardBase) {
    New-ItemProperty -Path $enrollKey -Name "DashboardBaseUrl" -Value $dashboardBase -PropertyType String -Force | Out-Null
}
if ($shortlink) {
    New-ItemProperty -Path $enrollKey -Name "Shortlink" -Value $shortlink -PropertyType String -Force | Out-Null
}

Log "enrolment complete"

# 9. Phone home: best-effort POST to the dashboard's mark-enrolled
#    endpoint so the parent doesn't have to click "Mark applied" by hand.
#    Uses the shortlink (`?dl=<code>`) for auth — same mechanism the
#    enrolment page already supports. The tunnel is up by now, so the
#    POST routes through the kid's WG connection; if the kid's PC is on
#    the dashboard's LAN it'll succeed even without the tunnel.
#
#    Failures here are non-fatal — the parent's manual button still works.
if ($dashboardBase -and $shortlink) {
    $markUrl = "$dashboardBase/api/devices/$wgIp/windows-mdm/mark-enrolled?dl=$shortlink"
    Log "phoning home: $markUrl"
    try {
        # TLS 1.2 explicit for old WinPS 5.1 sessions where Net.ServicePointManager defaults to SSL3/TLS 1.0.
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        # Brief retry loop — the tunnel may still be coming up at this point.
        $marked = $false
        for ($attempt = 1; $attempt -le 5; $attempt++) {
            try {
                Invoke-RestMethod -Uri $markUrl -Method POST -TimeoutSec 5 | Out-Null
                $marked = $true
                break
            } catch {
                Log "  attempt $attempt failed: $($_.Exception.Message)"
                Start-Sleep -Seconds 3
            }
        }
        if ($marked) {
            Log "dashboard auto-marked applied"
        } else {
            Log "could not auto-mark applied; click 'Mark applied' on the dashboard manually"
        }
    } catch {
        Log "phone-home setup failed: $_  (click 'Mark applied' on the dashboard manually)"
    }
} else {
    Log "no dashboard URL / shortlink baked in; skipping phone-home"
}
"""


# ---------------------------------------------------------------------------
# reconcile.ps1 — SYSTEM scheduled-task body.
#
# Boot + 5-minute repeating trigger. Re-asserts every piece of policy that
# apply.ps1 set up, so even if the kid (or some flaky update) knocks one
# of them out it heals on the next tick. Mirrors the nftables sidecar's
# reconcile loop philosophy.


RECONCILE_PS1 = r"""# gdlf Windows reconciliation task — runs every 5 minutes from a SYSTEM
# scheduled task. Idempotent re-assertion of enrolment state. No-ops in
# the steady state.

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$enrollKey = "HKLM:\Software\gdlf\Enrollment"
if (-not (Test-Path $enrollKey)) {
    # Not enrolled — task left over from a previous package? Exit quietly.
    exit 0
}

$cfg = Get-ItemProperty -Path $enrollKey
$tunnelName = $cfg.TunnelName
$svcName = "WireGuardTunnel`$$tunnelName"
$confPath = "C:\ProgramData\gdlf\$($cfg.ConfName)"
$expectedSha = $cfg.ConfSha256

function Log($msg) {
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Write-Host "[$stamp] [gdlf-reconcile] $msg"
}

# 1. Re-assert HKLM\Software\WireGuard\LimitedOperatorUI = 1.
$wgKey = "HKLM:\Software\WireGuard"
if (-not (Test-Path $wgKey)) {
    New-Item -Path $wgKey -Force | Out-Null
}
$current = (Get-ItemProperty -Path $wgKey -Name "LimitedOperatorUI" -ErrorAction SilentlyContinue).LimitedOperatorUI
if ($current -ne 1) {
    Log "restoring LimitedOperatorUI=1"
    New-ItemProperty -Path $wgKey -Name "LimitedOperatorUI" `
        -Value 1 -PropertyType DWord -Force | Out-Null
}

# 2. Re-assert the wg conf on disk matches what was baked into the .ppkg.
#    The reconcile task can't pull a new conf — only the parent re-issuing
#    a .ppkg can — but it can heal a tampered file.
if (Test-Path $confPath) {
    $actualSha = (Get-FileHash -Path $confPath -Algorithm SHA256).Hash.ToLower()
    if ($actualSha -ne $expectedSha.ToLower()) {
        Log "conf hash drift: have=$actualSha want=$expectedSha (tampered or rotated)"
        # We can't repair without the original — flag in event log only.
        # A re-issued .ppkg from the dashboard is the fix.
    }
} else {
    Log "conf missing at $confPath — cannot repair without re-enrolment"
}

# 3. Ensure the per-tunnel service is Running + Auto-start.
$svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
if (-not $svc) {
    Log "tunnel service $svcName not registered (was it uninstalled?)"
    exit 0
}
if ($svc.StartType -ne "Automatic") {
    Log "setting $svcName start type to Automatic"
    Set-Service -Name $svcName -StartupType Automatic
}
if ($svc.Status -ne "Running") {
    Log "restarting tunnel service $svcName (was $($svc.Status))"
    try { Start-Service -Name $svcName } catch { Log "start failed: $_" }
}
"""


def render_install_cmd() -> str:
    """The self-elevating .cmd entry point. Static — no per-device values."""
    return INSTALL_CMD


def render_uninstall_cmd() -> str:
    """The self-elevating .cmd entry point for the uninstall bundle."""
    return UNINSTALL_CMD


def render_apply(ctx: ScriptContext) -> str:
    """Substitute the ScriptContext fields into the APPLY_PS1 template.

    Uses simple `__NAME__` placeholders rather than Python f-strings so the
    raw PowerShell stays readable (and so the many `$`s don't have to be
    doubled up)."""
    return _substitute(APPLY_PS1, ctx)


def render_reconcile(ctx: ScriptContext) -> str:
    """Substitute the ScriptContext fields into the RECONCILE_PS1 template.

    reconcile.ps1 deliberately reads the runtime values out of the
    HKLM\\Software\\gdlf\\Enrollment registry key rather than baking them
    in at build time — keeps the script identical across re-issues and
    lets one PC carry a single reconciliation task even if the .ppkg is
    re-applied."""
    return RECONCILE_PS1


def _substitute(template: str, ctx: ScriptContext) -> str:
    return (
        template
        .replace("__KID_NAME__", _ps_string(ctx.kid_name))
        .replace("__DEVICE_NAME__", _ps_string(ctx.device_name))
        .replace("__WG_IP__", _ps_string(ctx.wg_ip))
        .replace("__TUNNEL_NAME__", _ps_string(ctx.tunnel_name))
        .replace("__CONF_FILENAME__", _ps_string(ctx.conf_filename))
        .replace("__CONF_SHA256__", _ps_string(ctx.conf_sha256))
        .replace("__CA_FILENAME__", _ps_string(ctx.ca_filename))
        .replace("__CA_SHA1__", _ps_string(ctx.ca_sha1))
        .replace("__MSI_FILENAME__", _ps_string(ctx.msi_filename))
        .replace("__PACKAGE_ID__", _ps_string(ctx.package_id))
        .replace("__DASHBOARD_BASE_URL__", _ps_string(ctx.dashboard_base_url))
        .replace("__SHORTLINK_CODE__", _ps_string(ctx.shortlink_code))
    )


def _ps_string(s: str) -> str:
    """Escape a value for safe substitution inside a PowerShell
    double-quoted string. Backticks escape `$ `" and backslash."""
    return (
        s.replace("`", "``")
         .replace('"', '`"')
         .replace("$", "`$")
    )


# ---------------------------------------------------------------------------
# Revocation script — bundled into the uninstall .ppkg the dashboard
# generates when the parent removes a Windows enrolment.


UNINSTALL_CMD = r"""@echo off
REM gdlf Windows un-enrolment — self-elevating UAC wrapper.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting Administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
set RC=%errorlevel%
echo.
if %RC% neq 0 (
    echo uninstall.ps1 exited with code %RC%.
) else (
    echo gdlf un-enrolment complete. You can close this window.
)
pause
exit /b %RC%
"""


REVOKE_PS1 = r"""# gdlf Windows un-enrolment — Administrator-context revoke script.
# Reverses everything install.ps1 did. Idempotent.

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$enrollKey = "HKLM:\Software\gdlf\Enrollment"
if (-not (Test-Path $enrollKey)) {
    Write-Host "[gdlf-revoke] not enrolled; nothing to revoke"
    exit 0
}

$cfg = Get-ItemProperty -Path $enrollKey
$tunnelName = $cfg.TunnelName
$confName = $cfg.ConfName
$caThumbprint = $cfg.CaThumbprint
$wgIp = $cfg.WgIp
$dashboardBase = ""
try { $dashboardBase = (Get-ItemProperty -Path $enrollKey -Name "DashboardBaseUrl" -ErrorAction SilentlyContinue).DashboardBaseUrl } catch {}
$shortlink = ""
try { $shortlink = (Get-ItemProperty -Path $enrollKey -Name "Shortlink" -ErrorAction SilentlyContinue).Shortlink } catch {}

# 0. Phone home FIRST while the tunnel is still up. Best-effort.
if ($dashboardBase -and $shortlink -and $wgIp) {
    $markUrl = "$dashboardBase/api/devices/$wgIp/windows-mdm/mark-enrolled?dl=$shortlink"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-RestMethod -Uri $markUrl -Method POST -TimeoutSec 5 | Out-Null
        Write-Host "[gdlf-revoke] dashboard auto-marked applied"
    } catch {
        Write-Host "[gdlf-revoke] phone-home failed: $($_.Exception.Message)  (click 'Mark applied' manually)"
    }
}

# 1. Stop + uninstall the tunnel service.
$wgExe = "$env:ProgramFiles\WireGuard\wireguard.exe"
if (Test-Path $wgExe) {
    Write-Host "[gdlf-revoke] removing tunnel service for $tunnelName"
    try {
        Start-Process -FilePath $wgExe `
            -ArgumentList "/uninstalltunnelservice $tunnelName" `
            -Wait -NoNewWindow | Out-Null
    } catch { Write-Host "[gdlf-revoke] uninstalltunnelservice failed: $_" }
}

# 2. Remove the reconcile scheduled task.
Unregister-ScheduledTask -TaskName "gdlf-reconcile" -Confirm:$false `
    -ErrorAction SilentlyContinue

# 3. Remove the CA from LocalMachine\Root.
if ($caThumbprint) {
    $store = New-Object System.Security.Cryptography.X509Certificates.X509Store(
        "Root", "LocalMachine")
    $store.Open("ReadWrite")
    foreach ($c in $store.Certificates) {
        if ($c.Thumbprint -eq $caThumbprint) {
            Write-Host "[gdlf-revoke] removing CA $caThumbprint"
            $store.Remove($c)
        }
    }
    $store.Close()
}

# 4. Remove ProgramData\gdlf (conf + reconcile.ps1).
if (Test-Path "C:\ProgramData\gdlf") {
    Remove-Item -Path "C:\ProgramData\gdlf" -Recurse -Force `
        -ErrorAction SilentlyContinue
}

# 5. Drop the enrolment registry keys.
Remove-Item -Path "HKLM:\Software\gdlf" -Recurse -Force `
    -ErrorAction SilentlyContinue

# 6. Reset LimitedOperatorUI (leaves WireGuard installed but usable again
#    for any non-gdlf tunnel the user might want to add later).
$wgKey = "HKLM:\Software\WireGuard"
if (Test-Path $wgKey) {
    Remove-ItemProperty -Path $wgKey -Name "LimitedOperatorUI" `
        -ErrorAction SilentlyContinue
}

Write-Host "[gdlf-revoke] done"
"""


def render_revoke() -> str:
    """Static revoke script — reads everything it needs from the on-device
    Enrollment registry key written by apply.ps1."""
    return REVOKE_PS1
