# blockpage

Tiny stdlib-only HTTP server that returns "you're blocked" HTML for any
request. Used as the user-facing target for two kinds of block:

| Port | When it serves                            | Page                          |
| ---- | ----------------------------------------- | ----------------------------- |
| 8888 | Schedule block (out of allowed hours)     | "You shall not pass" + static text about schedule |
| 8889 | DNS sinkhole — kid hit a blocked domain   | "You shall not pass" + the blocked Host header   |

Both pages embed the same Gandalf image as a `data:` URL so they render
without any extra network round-trip (the device is on a blocked URL —
no other origin is reachable through the tunnel mid-block).

## Why two ports

nftables routes the two cases differently:

```
# Schedule block (kid is in @blocked_clients):
iifname "wg0" ip saddr @blocked_clients tcp dport 80 dnat ip to 127.0.0.1:8888

# DNS sinkhole (AdGuard returned 10.13.13.254 for a blocked domain):
iifname "wg0" ip daddr 10.13.13.254 tcp dport 80 dnat ip to 127.0.0.1:8889
```

Same socket, different ports → distinct messaging without needing
`SO_ORIGINAL_DST` plumbing. The sinkhole page reads the `Host:` header
to show *which* domain was blocked.

## Why it lives in the wg netns

Compose: `network_mode: "service:wg"`. Reasons:

* nftables targets `127.0.0.1:8888/8889` — the listener has to be in the
  same netns as the DNAT rule.
* AdGuard already lives here and binds `:53/:80`; we don't conflict.
* No SNAT in the path — we could attribute the request to a kid if we
  wanted (today we don't bother on the schedule page).

## HTTPS limitation

This server is HTTP-only. HTTPS to the AdGuard sinkhole IP gets a TCP RST
from nftables (no TLS cert we could present to a browser without the
mitmproxy CA installed). Sites that force HSTS (most major sites) will
just show "Connection refused" instead of the friendly page. To improve
that, terminate TLS here with the mitmproxy CA — only useful for devices
that have it trusted.

## Files

| File         | What                                                          |
| ------------ | ------------------------------------------------------------- |
| `server.py`  | Two `ThreadingHTTPServer` instances; shared CSS via `_CARD_STYLE`. |
| `gandalf.png`| 280px-wide PNG (74KB), copied into the image, loaded at startup, encoded as `data:image/png;base64,...`. |
| `Dockerfile` | `python:3.12-alpine` + `COPY server.py /server.py` + `COPY gandalf.png /gandalf.png`. |

## Gotchas

* **The Dockerfile must COPY gandalf.png explicitly.** If you change the
  template substitution and forget the copy, `_gandalf_data_url()` returns
  `""` and the pages render without an image — silently.

* **`SINKHOLE_TEMPLATE` is `.format()`d** to inject the Host header. Every
  literal CSS `{` / `}` is doubled. If you ever add a new placeholder,
  make sure unrelated braces are still escaped — same class of bug that
  bit the mitmproxy block page.

* **`SCHEDULE_HTML` is pre-rendered bytes** — no placeholders, no risk.
  Use this pattern for any new static pages.

## Testing

```bash
docker exec gdlf-wg curl -s http://127.0.0.1:8888/ | grep "<h1>"
docker exec gdlf-wg curl -s -H 'Host: pornhub.com' http://127.0.0.1:8889/ \
  | grep -E "<h1>|<code>"
```
