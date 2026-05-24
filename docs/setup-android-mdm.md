# Android MDM setup (Android Management API)

This is the **simplest path** to lock an Android phone into gdlf: WireGuard
always-on with lockdown, mitmproxy CA system-trusted (every app sees it),
and the kid can't add another VPN, factory-reset, or uninstall the tunnel.

Total time: ~15 minutes one-time setup + ~5 minutes per enrolled device.
Cost: free.

The phone **must be factory-reset** to enrol (Android requirement for
Device Owner mode). If you can't wipe a device, fall back to the manual
WireGuard + CA flow on the device's enrol page.

---

## One-time setup

### 1. Create a Google Cloud project

Open [console.cloud.google.com](https://console.cloud.google.com/) → top
bar → project dropdown → **New Project**. Name it anything you like
(e.g. `gdlf-mdm`).

No billing account is required.

### 2. Enable the Android Management API

Go to
[the API library page for Android Management API](https://console.cloud.google.com/apis/library/androidmanagement.googleapis.com),
make sure your new project is selected in the top bar, click **Enable**.

### 3. Create a service account

Go to
[IAM & Admin → Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts).

- Click **Create service account**
- Name: `gdlf-rules-svc` (anything is fine)
- Click **Create and continue**
- In the role picker, grant **Android Management User**
  (`roles/androidmanagement.user`)
- Click **Done**

### 4. Download the JSON key

Click the service account you just made → **Keys** tab → **Add key** →
**Create new key** → **JSON** → **Create**. A `.json` file downloads.

Move it into the gdlf state directory:

```
mv ~/Downloads/<project>-<id>.json config/state/amapi/service-account.json
chmod 600 config/state/amapi/service-account.json
```

The `config/state/amapi/` directory is created automatically by
`./gdlf amapi init` — run that first if it doesn't exist.

### 5. Bind an Android Enterprise

This is the EMM-side container your devices enrol into. Bind it to a
Google account you own.

```
./gdlf amapi enterprise signup https://httpbin.org/get
```

The command prints a JSON blob containing a `url` and a `name`. The
callback URL must be HTTPS (Google's requirement), but it doesn't actually
need to host anything — `https://httpbin.org/get` is convenient because
it echoes back the request, so the `enterpriseToken` query parameter is
easy to copy from the response.

Open the printed `url` in your browser:

- Sign in with the Google account you want to own this Android Enterprise
- Accept the agreement
- Google redirects to the callback URL (`httpbin.org/get`) with
  `?enterpriseToken=ENT_xxxxxxxxx` in the URL bar
- Copy the `ENT_xxxxxxxxx` value

> ⚠️ The signup URL is single-use. If you accidentally lose the token
> before copying it, just run `./gdlf amapi enterprise signup` again.

### 6. Finalise the enterprise

Take the `enterpriseToken` from the previous step AND the `name` field
from the JSON the signup command printed (looks like `signupUrls/Cxxxxxxxxx`),
and run:

```
./gdlf amapi enterprise complete ENT_xxxxxxxxx signupUrls/Cxxxxxxxxx
```

This writes `config/state/amapi/enterprise.json`. You're done.

### 7. Verify

```
./gdlf amapi status
```

Should show both files present and print your enterprise resource name
(`enterprises/LCxxxxxxxxx`).

If you set this up *after* the stack was already running, rebuild
rules-svc so it picks up the new credentials:

```
./gdlf rebuild rules-svc
```

---

## Per-device enrolment

This is the part the parent does each time they hand a new Android phone
to a kid.

### 1. Open the enrolment flow in the dashboard

Two equivalent entry points:

- **Device row** on a kid's page → click the **MDM** button
- **Per-device enrol page** (`/kids/<name>/devices/<ip>/enrol`) → the
  Android MDM card is at the top

Click **Generate enrolment QR**. A QR code appears.

> The QR is single-use and valid for 1 hour. If setup fails partway,
> click **Regenerate**.

### 2. Factory-reset the phone

If the phone is brand new: skip ahead.

If the phone is already in use:

- Settings → System → Reset options → Erase all data (factory reset)
- Confirm. The phone reboots into the welcome screen.

Device Owner mode (the full management mode we need) can only be set
during initial provisioning. There is no way to retrofit it onto an
already-set-up phone.

### 3. Open the QR scanner

On the welcome screen ("Hi there, let's set up your..."):

- **Tap six times in the same spot on the screen**
- A QR scanner opens

(If the QR scanner doesn't appear, the phone may need Wi-Fi connected
first — back up, join Wi-Fi, then try the six-tap again on the next
screen.)

### 4. Scan the QR

Point the camera at the QR in the dashboard. The phone:

1. Downloads "Android Device Policy" (Google's DPC app)
2. Enrols itself as Device Owner in your enterprise
3. Applies the gdlf policy: installs WireGuard with your tunnel config,
   pins it as always-on with lockdown, installs the mitmproxy CA as
   system-trusted, blocks bypass paths

This takes ~2 minutes. The phone may reboot once.

### 5. Confirm enrolment

Back on the dashboard, the device status flips from **pending** to
**active** within ~60 seconds (the next status poll).

On the phone, you can verify:

- Settings → Network & Internet → VPN → "gdlf" is listed, marked as
  always-on, and the toggle is greyed out
- Settings → Network & Internet → VPN → trying to add another VPN is
  blocked
- Settings → System → Reset → factory reset is blocked
- Open any HTTPS site in Chrome — no certificate warnings (the mitm
  CA is system-trusted)

---

## Ongoing operations

### Policy updates propagate automatically

When you edit URL rules, schedule, or blocked services in the dashboard,
rules-svc patches the AMAPI policy and the phone picks up the change
within a few minutes. No manual step needed.

For an immediate push, open the MDM dialog and click **Re-push policy**.

### Removing a device

In the MDM dialog: **Unenroll**. This:

1. Calls `enterprises.devices.delete` on AMAPI
2. Deletes the device's policy
3. Clears `android_mdm` from kids.yaml

The phone factory-resets itself when it next checks in (this is Android's
behaviour for Device Owner devices that get unenrolled — there's no
graceful exit).

### What if it gets stuck on "pending"?

Click **Refresh status** in the MDM dialog. If the device shows up in
the GCP console under
[Android Management → Devices](https://console.cloud.google.com/apis/api/androidmanagement.googleapis.com/credentials)
but the dashboard doesn't see it, check rules-svc logs for AMAPI errors
(`./gdlf logs rules-svc | grep amapi`).

---

## Why we don't use Family Link / "user-installed CA" / etc.

- **Family Link** has no API — purely interactive, can't be integrated.
- **User-installed CA** (Settings → Security → Install certificate) only
  affects apps that opt in via `networkSecurityConfig` — almost no app
  does. Most HTTPS traffic stays opaque to mitmproxy.
- **Always-on VPN toggle without MDM** (in Settings) can be reverted by
  the kid in one tap.

AMAPI is the only path that gives system-wide CA trust + a non-removable
always-on VPN. It's also the only path that doesn't require us to ship
and sign our own Android app.
