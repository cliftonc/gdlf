# nftables sidecar

Alpine + Python + `nft`. A single long-running loop that reads
`config/kids.yaml`, computes the desired ruleset, and applies it inside
the wg container's network namespace.

## Why it shares wg's netns

`network_mode: "service:wg"` in compose. The rules use `iifname "wg0"`
to match traffic emerging from the WireGuard interface — wg0 only exists
in wg's netns. Sharing the namespace also means nft sees the same
loopback interface that AdGuard / mitmproxy / blockpage bind to, so DNAT
to `127.0.0.1:<port>` actually lands somewhere.

## The control loop

`reconcile.py` runs forever:

```
every INTERVAL (default 30s):
  cfg     = yaml.safe_load(kids.yaml)
  ruleset = render(cfg, now())
  if hash(ruleset) != last_hash:
    nft -f - < ruleset    # atomic replace via 'destroy table; table ... {...}'
```

The ruleset is regenerated as a whole each cycle, with `destroy table inet
gdlf` as the first statement. `nft -f` reads the entire input as one
transaction, so this is atomic — there's no window where the firewall is
empty.

## What the ruleset does

```
table inet gdlf {
  set blocked_clients { ip addrs whose schedule says "out of allowed hours" right now }
  set mitm_clients    { ip addrs whose mitm_ca_installed == true }
  set doh_dot_ips     { well-known DoH/DoT resolver IPs — DOH_DOT_IPS in reconcile.py }

  chain prerouting {  # NAT prerouting (DNAT)
    iifname wg0 udp/tcp dport 53                       → 127.0.0.1     (AdGuard listening in this NS)
    iifname wg0 @blocked_clients tcp dport 80          → 127.0.0.1:8888 (schedule block page)
    iifname wg0 ip daddr 10.13.13.254 tcp dport 80     → 127.0.0.1:8889 (DNS-sinkhole block page)
    iifname wg0 @mitm_clients tcp dport 80             → 127.0.0.1:8080 (mitmproxy)
    iifname wg0 @mitm_clients tcp dport 443            → 127.0.0.1:8080 (mitmproxy)
  }

  chain forward {  # filter
    iifname wg0 @blocked_clients tcp dport 443       reject with tcp reset    # HTTPS: fast-fail
    iifname wg0 @blocked_clients tcp dport != 80     drop                     # other TCP: silent
    iifname wg0 @blocked_clients meta l4proto udp    drop                     # UDP (QUIC, etc.)
    iifname wg0 @mitm_clients   udp dport 443        reject                   # force QUIC → TCP for inspection
    iifname wg0 ip daddr 10.13.13.254 tcp dport 443  reject with tcp reset    # sinkhole HTTPS fast-fail
    iifname wg0 ip daddr @doh_dot_ips tcp dport {443,853} reject with tcp reset   # block DoH/DoT, force fallback to system DNS
    iifname wg0 ip daddr @doh_dot_ips udp dport {443,853} drop                    # same, UDP variants
  }

  chain postrouting {  # NAT srcnat
    ip saddr 10.13.13.0/24 oifname != "wg0" masquerade
  }
}
```

## Key design choices

* **DNS to `127.0.0.1`** (not AdGuard's bridge IP) because adguard binds
  `0.0.0.0:53` in this same namespace. Requires `net.ipv4.conf.all.route_localnet=1`
  (set in compose `sysctls`).
* **Per-kid identity via WG IP.** Every set membership decision is keyed
  on the source IP arriving on wg0. There's no other auth.
* **`reject with tcp reset` on blocked HTTPS / sinkhole HTTPS** so browsers
  show "Connection refused" instantly rather than spinning until the TCP
  SYN times out.
* **Force QUIC → TCP for mitm clients.** Modern browsers prefer HTTP/3
  for major sites. Without this, mitmproxy would only see DNS for any
  QUIC-capable destination and the activity log would be useless.
* **MASQUERADE only on egress (`oifname != "wg0"`).** Local traffic stays
  unmolested so AdGuard / mitm / blockpage see the kid's real source IP.

## Files

| File           | What                                                         |
| -------------- | ------------------------------------------------------------ |
| `Dockerfile`   | `alpine:3.20` + nftables + python3 + pyyaml + iproute2.      |
| `reconcile.py` | The loop. Pure Python, no extra deps beyond pyyaml.          |

## Gotchas

* **nft `inet` table needs `dnat ip to`** (not `dnat to`) when the address
  is unambiguous-looking but the table is `inet` (IPv4 + IPv6). Bare
  `dnat to 10.42.0.10:53` fails with "specify `dnat ip' or 'dnat ip6'".

* **`udp drop` is invalid syntax.** Must be `meta l4proto udp drop`. `tcp
  drop` etc. also need an l4proto qualifier or a port (`tcp dport != 80
  drop` works because the protocol comes from `tcp`).

* **wg restart orphans this container.** Because we share wg's netns, when
  wg's netns is destroyed (container restart) we keep a stale handle to
  the old netns and lose all network. `docker restart gdlf-nft` re-attaches.
  Same hazard for adguard, mitmproxy, blockpage.

* **No conntrack-tools by default.** `apk add conntrack-tools` if you
  need to inspect NAT state for debugging.

* **Schedule TZ.** `now_local()` uses container TZ (set by compose env).
  Always pass `TZ=Europe/London` (or wherever) so daily schedules match
  what the parent expects.

## Testing

```bash
# See the live ruleset
docker exec gdlf-nft nft list table inet gdlf

# Force a single render to stdout (no apply)
docker exec gdlf-nft python3 -c "
import datetime, importlib.util
spec = importlib.util.spec_from_file_location('r', '/usr/local/bin/reconcile.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(m.render(m.load_kids(), datetime.datetime.now()))
"

# Test schedule windows
docker exec gdlf-nft python3 -c "
import datetime, importlib.util
spec = importlib.util.spec_from_file_location('r', '/usr/local/bin/reconcile.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
w = m.parse_windows('07:00-21:00,22:00-23:00')
print(m.in_window(datetime.datetime(2026,5,25,8,0), w))   # True
print(m.in_window(datetime.datetime(2026,5,25,21,30), w)) # False
"
```
