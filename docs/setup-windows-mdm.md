# Windows enrolment (downloadable .zip)

The Windows path differs from Apple / Android by design — there's **no
live MDM channel**. The dashboard builds a downloadable `.zip`; the parent
extracts it on the kid's PC and runs `Install.cmd` as Administrator; the
script installs the gdlf CA, the WireGuard for Windows client, registers
the WireGuard tunnel as an always-on **Windows service** (kernel kill-
switch + autostart pre-logon + service ACL the kid can't override), and
drops a SYSTEM scheduled task that re-asserts state every 5 minutes.

Total time: ~5 min one-time setup + ~5 min per enrolled PC.
Works on **Windows 10 / 11 — Home, Pro, Education, Enterprise**.
Cost: free.

Re-enrolling means generating a fresh `.zip` and running it again.

> **Note on the format.** We originally tried to ship a `.ppkg` (Windows
> Provisioning Package), but `.ppkg` is internally a WIM archive with a
> compiled multi-XML structure that only Microsoft's icd.exe / Windows
> Configuration Designer can produce — building one from scratch in
> Python is not viable. So we deliver a plain `.zip` with a self-elevating
> `Install.cmd` inside that the parent runs as Administrator. The
> on-device behaviour is identical; the only thing lost is the
> Settings.app "Add a provisioning package?" UX wrapping.

---

## Containment model — read this first

There is no "Device Owner" mode on Windows the way there is on Android.
The containment boundary for the kid is **Standard User on the PC, with
the parent as the sole local Administrator.**

The .zip makes no attempt to demote existing accounts. **Set up the
accounts first**:

- The parent's account: **Administrator**.
- The kid's account: **Standard user**. (Windows Settings → Accounts →
  Family & other users → Change account type → Standard user.)
- Sign in as the parent to run Install.cmd; the kid signs in normally
  afterwards.

Why this works: the per-tunnel WireGuard Windows service is ACL'd so a
Standard User cannot stop it, the `LimitedOperatorUI` registry flag we
set hides Edit/Remove from the WG tray UI, and the kid can't
`Unregister-ScheduledTask`, `certutil -delstore Root`, or write into
`C:\ProgramData\gdlf` without admin creds.

---

## One-time appliance setup

### 1. Download the WireGuard for Windows installer

```
./gdlf windows init
```

Prints the upstream URL + pinned sha256. Copy-paste the `curl` line it
shows; verify the checksum. The MSI ends up at
`config/state/windows/wireguard.msi` and is bundled (~10 MB) into every
per-device `.zip`.

If the pinned sha256 doesn't match upstream, check
[download.wireguard.com/windows-client/](https://download.wireguard.com/windows-client/)
for a newer release and bump `WG_MSI_VERSION` + `WG_MSI_SHA256` in the
`gdlf` script's `windows)` case branch.

### 2. Confirm + bring the stack up

```
./gdlf windows status     # WG MSI should say "ok"
./gdlf up
```

---

## Per-device enrolment

### 1. Prep the kid's PC

- Create / confirm the kid's account is **Standard user**.
- Sign in as the **Administrator** (the parent's account).
- If the kid was using their account as Administrator before, demote
  them via Settings → Accounts → Family & other users → Change account
  type → Standard user. (Windows will not let you demote the only
  Administrator, so the parent's account needs to be Admin first.)

### 2. Add the device in the dashboard

From the gdlf dashboard, open the kid → add a new device with
**Platform: Windows**, name it (e.g. "alice-laptop"). The wizard
allocates a WireGuard IP + keypair as usual.

### 3. Build the .zip

On the device's row, open the **MDM** dialog → **Build .zip** (or use
the enrolment page's bundle card). The dashboard:

- Renders per-device `Install.cmd`, `install.ps1`, `reconcile.ps1` from
  templates with the kid name, WG IP, tunnel name, conf hash, and CA
  fingerprint baked in.
- Bundles the WG MSI, the mitmproxy CA (DER), the per-kid wg-quick conf,
  and a `README.txt` into a single `.zip` via Python's `zipfile`.
- Returns a one-time download URL.

### 4. Transfer + run

Download `gdlf-install-<wg_ip>.zip`. Copy it to the kid's PC (USB stick,
OneDrive, network share, whatever). On the kid's PC, signed in as
**Administrator**:

- Right-click the zip → **Extract All** (any location is fine).
- In the extracted folder, **right-click `Install.cmd` → Run as
  administrator** (or just double-click `Install.cmd` and click **Yes**
  at the UAC prompt).
- A console window opens, runs ~30s, prints "gdlf enrolment complete",
  and asks you to press a key.

There's no progress bar; you'll see the WireGuard tray icon appear when
the tunnel comes up.

### 5. Verify on the device (still as Admin)

Open PowerShell:

```
# Tunnel service should be Running, StartType Automatic.
Get-Service "WireGuardTunnel`$gdlf-*"

# gdlf CA should appear in LocalMachine\Root.
Get-ChildItem Cert:\LocalMachine\Root | Where-Object Subject -like "*gdlf*"

# Reconcile task should exist + be Ready.
Get-ScheduledTask -TaskName "gdlf-reconcile"

# Limited tray UI flag should be 1.
Get-ItemProperty "HKLM:\Software\WireGuard" -Name LimitedOperatorUI

# Enrolment metadata stamped by install.ps1.
Get-ItemProperty "HKLM:\Software\gdlf\Enrollment"
```

### 6. Verify the containment

Sign out, sign back in as the **kid (Standard user)**, and try to:

- Stop the WG service → "Access is denied".
- Edit / Remove the tunnel from the WireGuard tray UI → buttons hidden.
- Open `C:\ProgramData\gdlf\<tunnel>.conf` → "Access is denied".

Reach a site you've URL-rule-blocked from the dashboard. The block page
should show. Reach a normal site to confirm general traffic flows.

### 7. Confirm in the dashboard

`install.ps1` phones home at the end of installation — POSTs a
`mark-enrolled?dl=<shortlink>` to the dashboard URL the .zip was built
from. If that POST succeeds (LAN deployments, or remote deployments
where the kid's PC has internet via the just-installed tunnel), the
device flips from `pending` to `enrolled` automatically within ~5
seconds of "gdlf enrolment complete".

If the phone-home fails (no route to dashboard, firewall, etc.) the
script logs it in the console window and exits successfully — you can
still click **Mark applied** by hand on the device's page in the
dashboard.

---

## Re-enrolling / pushing a policy update

There is no live `/mdm/checkin` for Windows. Any change that affects the
per-kid WireGuard conf or the mitmproxy CA needs a fresh `.zip`.

In practice this is rare — the conf rarely changes, and the CA never
changes. When it does:

1. Dashboard → device → MDM → **Build .zip**.
2. Run the new `.zip` on the kid's PC (same flow). `install.ps1` is
   idempotent: it overwrites the conf, re-imports the CA (idempotent in
   the cert store), reinstalls the tunnel service via
   `wireguard.exe /installtunnelservice` (also idempotent).

The SYSTEM reconcile task takes care of routine drift (service stopped,
LimitedOperatorUI cleared, etc.) on a 5-minute tick.

---

## Un-enrolling

Dashboard → device → MDM → **Remove Windows enrolment**. This builds a
matching uninstall `.zip` and returns a download link.

Run that .zip on the kid's PC the same way as the enrol one
(right-click `Uninstall.cmd` → Run as administrator). Under the hood,
`uninstall.ps1` runs as Administrator and:

- `/uninstalltunnelservice` for the gdlf tunnel.
- Unregisters the `gdlf-reconcile` scheduled task.
- Removes the gdlf CA from `LocalMachine\Root` by thumbprint.
- Removes `C:\ProgramData\gdlf\`.
- Clears `HKLM\Software\gdlf` + `LimitedOperatorUI`.

WireGuard itself is left installed.

After the parent confirms with **Mark applied**, the device's
`WindowsMdmState` is cleared from `kids.yaml`.

---

## Known gotchas

- **Firefox uses its own cert store** — it ignores `LocalMachine\Root`,
  so HTTPS interception breaks the moment the kid switches to Firefox.
  Either pin Edge / Chrome (which both use the system store) by hiding
  the Firefox installer, or set the `security.enterprise_roots.enabled`
  Firefox policy to `true` via a Firefox policies.json drop — that flips
  Firefox to use the Windows store too. Out of scope for the .zip
  itself.

- **Safe Mode bypasses the WG service start** — a kid who knows to boot
  Safe Mode escapes the tunnel. Defence: BIOS password + BitLocker
  recovery key not given to the kid. Out of scope for the .zip.

- **DoH/DoT in the browser** still works around AdGuard's DNS layer. The
  mitmproxy URL-rule layer catches it via SNI inspection — same caveat
  as on iOS / Android.

- **Microsoft Family Safety is not a substitute.** It only filters Edge
  and explicitly cannot enforce VPN policy.

- **SmartScreen may warn about the .zip / Install.cmd** the first time
  a parent runs it. The file isn't from a known publisher — the
  Administrator clicks **More info → Run anyway** (same gesture as
  approving the UAC prompt). If your AV is aggressive about
  `Start-Process -Verb RunAs` patterns, you may need to whitelist
  `Install.cmd`.
