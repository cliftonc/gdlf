# Apple iOS MDM setup

This is the path to lock an iPhone (or iPad) into gdlf: WireGuard always-on
(no user override), mitmproxy CA system-trusted, and the kid can't add
another VPN, install another profile, or factory-reset to escape.

This is more setup than the Android path because Apple does *not* host
the MDM server — you do. That means we need our own signing CA, an Apple
APNs Push Certificate, and a public TLS endpoint that supports mTLS.

Total time: ~30 minutes one-time setup + ~10 minutes per enrolled device
(plus a factory reset and an Apple Configurator session on a Mac).

Cost: free if you already have an Apple ID, a domain on Cloudflare, and a
public IP. The APNs cert renewal is annual but free.

The phone **must be supervised** — meaning wiped and re-set-up via Apple
Configurator on a Mac. There is no way to retrofit supervision onto an
already-set-up phone.

---

## Prerequisites

- A **Mac** with [Apple Configurator 2](https://apps.apple.com/us/app/apple-configurator-2/id1037126344)
  installed (free)
- An **Apple ID** (developer account is *not* required, but recommended
  for fewer renewal hassles)
- A **domain you control via Cloudflare** (or the willingness to swap
  out the Caddy DNS-01 plugin for another provider)
- A **public IP / port reachable from the internet** on a port you can
  forward to gdlf (default: TCP/8081)
- A free account at [mdmcert.download](https://mdmcert.download/)
  (Apple gates direct CSR submission through a small set of registered
  intermediaries; this is the most popular one)

---

## One-time setup

### 1. Pick a public hostname

Pick a hostname under your Cloudflare-managed zone for the MDM endpoint,
e.g. `gdlf.example.com`. Add an A record pointing to your home WAN IP.

### 2. Create a Cloudflare API token

Cloudflare → My Profile → API Tokens → **Create token** →
**Create Custom Token**.

- Permissions: **Zone → DNS → Edit**
- Zone Resources: include the zone for your hostname
- Click **Create**, copy the token

### 3. Forward the public port

On your home router, forward TCP `8081` (or whatever you pick) on the
WAN side to your gdlf host on `8081`.

### 4. Configure `.env`

Edit `.env` and set:

```
MDM_HOSTNAME=gdlf.example.com
MDM_BASE_URL=https://gdlf.example.com:8081
ACME_EMAIL=you@example.com
CADDY_HTTPS_PORT=8081
CADDY_TLS_ISSUER=acme
CLOUDFLARE_API_TOKEN=<token from step 2>
```

Setting `MDM_HOSTNAME` automatically enables the `mdm` compose profile,
which adds the Caddy front-door container.

### 5. Generate the gdlf MDM signing CA

This is the CA that signs per-device identity certs (devices present
these certs back to Caddy via mTLS).

```
./gdlf mdm-ca init
```

This is a one-time, 10-year self-signed CA at
`config/state/mdm-ca/{ca.pem,ca.key}`. Rotating it invalidates every
enrolled device, so don't.

### 6. Get an Apple APNs Push Certificate

Apple requires every MDM server to authenticate to APNs with a cert
they've signed. This is the most fiddly step because of the
mdmcert.download intermediary.

**6a. Register at mdmcert.download** (free):

[mdmcert.download/registration](https://mdmcert.download/registration).

**6b. Generate the two RSA keypairs:**

```
./gdlf apns csr
```

Creates:
- `config/state/apns/push.{key,csr}` — the keypair that becomes the
  long-lived APNs identity. **Never rotate `push.key` after enrolment** —
  it invalidates every device.
- `config/state/apns/encrypt.{key,pem}` — disposable keypair used only
  to decrypt mdmcert.download's response email.

**6c. Submit the CSR:**

```
./gdlf apns submit you@example.com
```

(Use the email you registered at mdmcert.download.) Wait a few minutes;
mdmcert.download will email you an encrypted attachment named something
like `mdm_signed_request.20260520_123456_1234.plist.b64.p7`.

**6d. Decrypt the attachment:**

```
./gdlf apns decrypt ~/Downloads/mdm_signed_request.*.plist.b64.p7
```

This writes `config/state/apns/push.plist.b64`.

**6e. Upload the plist to Apple:**

Go to [identity.apple.com/pushcert](https://identity.apple.com/pushcert/).
Sign in with your Apple ID. Click **Create a Certificate**, accept the
ToS, upload the `push.plist.b64` file. Apple gives you back a `.pem`.

Save that file as:

```
config/state/apns/push.pem
```

**6f. Confirm:**

```
./gdlf apns status
```

Should list all six files (key, csr, encrypt.{key,pem}, plist.b64, pem)
and print the push cert's subject + validity.

> ⚠️ APNs certs expire after **one year**. Around 30 days before expiry,
> repeat steps 6c–6e to renew. The same `push.key` is reused; only the
> cert refreshes. Renew *before* expiry — letting it lapse invalidates
> every enrolled device.

### 7. Bring up the stack

```
./gdlf up
```

Caddy will obtain a Let's Encrypt cert for `gdlf.example.com` via the
Cloudflare DNS-01 plugin. Watch `./gdlf logs caddy` for the
`certificate obtained successfully` line.

### 8. Smoke-test the TLS endpoint

From outside your network (or via your phone on cellular):

```
curl -i https://gdlf.example.com:8081/mdm/foo
# → expect 404 from rules-svc. The TLS chain (openssl s_client) should
#   be Let's Encrypt E* → ISRG, no warnings.
```

If you get a TLS error, fix routing / cert issuance first — devices won't
enrol against a broken endpoint.

---

## Per-device enrolment

You'll do this on a Mac with the iPhone cabled in. Plan for ~10 minutes
per device.

### 1. Generate an enrolment URL

In the dashboard, open the device's row on the kid's page → click **MDM**.
In the dialog that opens, click **Generate enrolment URL**. Copy the URL.

> The URL is single-use and valid for 30 minutes.

### 2. Open Apple Configurator 2 on the Mac

- Plug the iPhone into the Mac via USB. Tap **Trust** on the phone.
- In Configurator: **Prepare** → **Manual Configuration**
- Tick **Supervise devices**
- Tick **Allow devices to pair with other computers**
  (otherwise the phone forgets your Mac after enrolment)
- Click **Next**

### 3. Enrol in your MDM server

When asked which MDM server to enrol with:

- **Server name:** `gdlf`
- **Server URL:** paste the enrolment URL from step 1
- Click **Next**

Configurator validates the URL and downloads the profile. Continue
through the next screens (don't sign in to an Apple Business Manager
account — skip that).

### 4. Confirm and let the wipe begin

Configurator will warn you the device will be **wiped**. Confirm. The
phone reboots into setup mode and goes through automated provisioning,
which takes ~5 minutes.

### 5. Complete iOS setup on the phone

The phone runs through the normal "Hello" setup wizard. Hand it back to
the kid and let them complete:

- Wi-Fi
- Apple ID (kid's own, ideally via Family Sharing)
- Face ID / Touch ID
- Passcode

The MDM profile is already installed at this point — it's not optional.

### 6. Verify enrolment

Back in the dashboard, the MDM status flips from **pending** to
**enrolled** within seconds of the first device check-in. Then,
automatically:

- An `InstallProfile` command is queued
- An APNs push wakes the device
- The device pulls + installs the baseline profile (WireGuard always-on,
  CA trust, restrictions)

On the phone you can verify:

- Settings → General → VPN & Device Management → "gdlf" profile is
  listed and marked supervised
- Settings → General → VPN → WireGuard is the configured VPN and the
  toggle is greyed out (always-on, user override disabled)
- Settings → General → About → Certificate Trust Settings → the gdlf CA
  is auto-trusted (no manual trust step needed — MDM-pushed roots are
  auto-trusted)
- Open the allowed browser (default: Chrome — see step 7 below) — no
  certificate warnings

### 7. Install the allowed browser

Browser containment is part of the baseline profile. By default Safari
is **disabled**, and every other known third-party browser (Firefox,
Brave, DuckDuckGo, Edge, Opera, …) is **blocked** at the App Store
install step. The dashboard's *Settings → Browser policy* picker
controls which one browser is allowed.

Apple MDM cannot force-install a free App Store app without Apple
Business Manager, which gdlf doesn't integrate today. So on the device
itself, after enrolment:

1. Note the allowed browser shown in the dashboard's *Settings → Browser
   policy* card (default: **Chrome**).
2. On the phone, open the App Store and install that browser. Every
   other browser will refuse to install with "restricted by your device
   administrator" — that's the policy working.
3. Open it once. The gdlf App Configuration payload binds on first
   launch, disabling Incognito / Sync / Sign-in (per the dashboard
   toggles).

If the parent sets *Allowed browser* to Safari, skip this step — Safari
becomes the allowed browser and stays on the device. If set to **None**,
the device has no browser at all and web access only happens via in-app
WebViews (which still flow through the WireGuard tunnel).

---

## Ongoing operations

### Re-pushing policy

If you rotate WireGuard keys, refresh the mitmproxy CA, or want to
re-apply restrictions, open the MDM dialog and click **Re-install policy**.

### Querying device info

The MDM dialog has buttons for **Query device info** and **Query
installed apps**. Responses come back asynchronously — watch the
command queue panel in the dialog.

### Renewing the APNs cert (annually)

About 30 days before expiry, repeat steps 6c–6e of the one-time setup.
The same `push.key` is preserved; only the cert refreshes. Then
`./gdlf restart rules-svc` to pick up the new cert.

### Removing a device

There is no "unenroll" button right now. To remove:

- Either factory-reset the phone via Apple Configurator (it's still
  supervised, so this works from the Mac side)
- Or delete the device in the dashboard, which removes its kids.yaml
  row but leaves the phone's stale profile until you re-supervise

---

## Why we don't use a simpler path on iOS

- **Apple Business Manager / Apple Configurator + manual profile install**
  (without MDM) gives you the always-on VPN payload, but it can be
  removed by the user. Restrictions and lockdown bits only work in
  supervised mode, and supervision implies MDM.
- **MDM via a third-party SaaS (Jamf, Hexnode, etc.)** would work, but
  costs $$ per device and means trusting an external company with
  policy authority over your kids' phones.
- **No MDM**, just manual profile install: kid can disable VPN in
  Settings in two taps.

So if you want the same containment guarantees the Android path gives
you, the full Apple MDM dance is unavoidable.

---

## See also

- [setup-android-mdm.md](setup-android-mdm.md) — the equivalent for
  Android, much shorter because Google hosts the MDM server.
- [services/rules-svc/CLAUDE.md](../services/rules-svc/CLAUDE.md) —
  internals of the MDM implementation.
- [services/proxy/CLAUDE.md](../services/proxy/CLAUDE.md) — Caddy mTLS
  terminator details.
