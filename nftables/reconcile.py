#!/usr/bin/env python3
"""nftables reconciler.

Runs in the wg container's netns (`network_mode: service:wg`), so the
firewall rules apply to traffic emerging from wg0.

What we install:
  * DNAT :53/udp,tcp -> ADGUARD_IP
  * DNAT :80,:443/tcp -> MITM_IP  (transparent proxy)
  * `gdlf_kids` set + per-kid time-of-day drop rules

We rebuild the whole `gdlf` table atomically each cycle (`nft -f` reads
the table delete+create as one transaction), so we don't drift over time.

kids.yaml is the source of truth — mounted read-only. We don't write to it.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

KIDS_YAML = Path(os.environ.get("KIDS_YAML", "/etc/gdlf/kids.yaml"))
ADGUARD_IP = os.environ.get("ADGUARD_IP", "10.42.0.10")
MITM_IP = os.environ.get("MITM_IP", "10.42.0.11")
WG_SUBNET = os.environ.get("WG_SUBNET", "10.13.13.0/24")
TZ = os.environ.get("TZ", "UTC")

# Cycle interval (seconds). Schedules are minute-granular.
INTERVAL = int(os.environ.get("INTERVAL", "30"))


# Well-known DoH / DoT resolver IPs. Dropped at :443 and :853 so a kid who
# enables "Use secure DNS" in Chrome/Firefox falls back to system DNS (which
# goes through AdGuard). Without this, DoH silently bypasses every filter.
#
# IPv4 only for now. List is curated; add by editing here. Last refreshed
# 2026 — IPs change rarely but rotate sometimes. If a new resolver gains
# popularity, append it here.
DOH_DOT_IPS: tuple[str, ...] = (
    # Cloudflare 1.1.1.1
    "1.1.1.1", "1.0.0.1",
    "1.1.1.2", "1.0.0.2",        # malware-blocking
    "1.1.1.3", "1.0.0.3",        # malware+adult
    # Google Public DNS
    "8.8.8.8", "8.8.4.4",
    # Quad9
    "9.9.9.9", "149.112.112.112",
    "9.9.9.10", "149.112.112.10",
    "9.9.9.11", "149.112.112.11",
    # OpenDNS / Cisco
    "208.67.222.222", "208.67.220.220",
    "208.67.222.123", "208.67.220.123",  # FamilyShield
    # AdGuard public
    "94.140.14.14", "94.140.15.15",
    "94.140.14.15", "94.140.15.16",
    # ControlD anycast (their public endpoints)
    "76.76.2.0", "76.76.10.0",
    # NextDNS anycast (sample of well-known endpoints)
    "45.90.28.0", "45.90.30.0",
    # Comodo Secure DNS
    "8.26.56.26", "8.20.247.20",
    # Mullvad
    "194.242.2.2", "194.242.2.3",
)


def now_local() -> _dt.datetime:
    # Container TZ is set via env; localtime is correct.
    return _dt.datetime.now()


def is_weekend(d: _dt.date) -> bool:
    return d.weekday() >= 5


def parse_windows(spec: str) -> list[tuple[int, int]]:
    """'07:00-21:00,22:00-23:00' -> [(420, 1260), (1320, 1380)] minutes."""
    out: list[tuple[int, int]] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", chunk)
        if not m:
            print(f"[gdlf-nft] bad schedule {chunk!r}", file=sys.stderr)
            continue
        a = int(m.group(1)) * 60 + int(m.group(2))
        b = int(m.group(3)) * 60 + int(m.group(4))
        out.append((a, b))
    return out


def in_window(now: _dt.datetime, windows: list[tuple[int, int]]) -> bool:
    if not windows:
        return True
    mins = now.hour * 60 + now.minute
    for a, b in windows:
        if a <= b:
            if a <= mins < b:
                return True
        else:  # wraps midnight
            if mins >= a or mins < b:
                return True
    return False


def load_kids() -> dict:
    if not KIDS_YAML.exists():
        return {"kids": []}
    return yaml.safe_load(KIDS_YAML.read_text()) or {"kids": []}


def _parse_bonus(v) -> _dt.datetime | None:
    """kids.yaml may carry bonus_until as a native YAML timestamp (pyyaml
    returns datetime) or as an ISO-8601 string (depending on writer). Both
    are accepted; anything else is ignored."""
    if v is None:
        return None
    if isinstance(v, _dt.datetime):
        return v
    if isinstance(v, str):
        try:
            return _dt.datetime.fromisoformat(v)
        except ValueError:
            return None
    return None


def render(cfg: dict, now: _dt.datetime) -> str:
    blocked_ips: list[str] = []
    mitm_ips: list[str] = []
    for kid in cfg.get("kids", []) or []:
        kid_blocked = bool(kid.get("manual_block"))
        bonus_until = _parse_bonus(kid.get("bonus_until"))
        bonus_active = bonus_until is not None and bonus_until > now

        sched = (kid.get("schedule") or {})
        which = "weekend" if is_weekend(now.date()) else "weekday"
        allowed = (sched.get(which) or {}).get("allowed", "00:00-23:59")
        windows = parse_windows(allowed)
        # Bonus suspends schedule-based blocking. Manual blocks (kid-wide or
        # per-device) always win, regardless of bonus.
        out_of_window = (not in_window(now, windows)) and not bonus_active

        for d in kid.get("devices", []) or []:
            ip = d.get("wg_ip")
            if not ip:
                continue
            if kid_blocked or d.get("manual_block") or out_of_window:
                blocked_ips.append(ip)
            # Blocked devices stay in mitm_clients on purpose: mitmproxy will
            # serve our block page over HTTPS for them (see /api/decision).
            # Devices WITHOUT the CA can't see that page, so the forward chain
            # rejects 443 for `blocked AND NOT mitm` instead — see render().
            if d.get("mitm_ca_installed"):
                mitm_ips.append(ip)

    lines = [
        f"# rendered {now.isoformat()} TZ={TZ}",
        # Atomic replace: delete if exists, then add.
        "destroy table inet gdlf",
        "table inet gdlf {",
        f"    set blocked_clients {{ type ipv4_addr; flags interval; elements = {{ {', '.join(blocked_ips) or '0.0.0.0/32'} }}; }}",
        f"    set mitm_clients    {{ type ipv4_addr; elements = {{ {', '.join(mitm_ips) or '0.0.0.0'} }}; }}",
        f"    set doh_dot_ips     {{ type ipv4_addr; elements = {{ {', '.join(DOH_DOT_IPS)} }}; }}",
        "",
        "    chain prerouting {",
        "        type nat hook prerouting priority dstnat;",
        # DNS: AdGuard listens in this same netns on 0.0.0.0:53, so DNS
        # queries the kid sends to the wg gateway IP land there directly
        # with their original source IP preserved (= per-kid filtering works).
        # We also DNAT any rogue DNS attempts (e.g. hardcoded 8.8.8.8) back
        # to AdGuard on lo, so kids can't bypass by changing their device DNS.
        f"        iifname \"wg0\" udp dport 53 dnat ip to 127.0.0.1",
        f"        iifname \"wg0\" tcp dport 53 dnat ip to 127.0.0.1",
        # Schedule-blocked clients: HTTP requests get sent to the blockpage
        # server (127.0.0.1:8888 in this netns) so the device sees a friendly
        # "out of allowed hours" page instead of a silent timeout.
        f"        iifname \"wg0\" ip saddr @blocked_clients tcp dport 80 dnat ip to 127.0.0.1:8888",
        # DNS-blocked sinkhole: configure AdGuard's "Blocking mode" to
        # "Custom IP" => 10.13.13.254. AdGuard returns that IP for blocked
        # domains; HTTP gets routed to the DNS-block page (:8889) so the
        # kid sees "<host> is blocked" with the actual hostname (Host header).
        # HTTPS to the sinkhole gets a fast TCP reset below.
        f"        iifname \"wg0\" ip daddr 10.13.13.254 tcp dport 80 dnat ip to 127.0.0.1:8889",
        # HTTP/HTTPS only redirected to mitmproxy for devices whose
        # mitm_ca_installed is true. Other devices' :80/:443 flows out via
        # masquerade — HTTPS works normally without CA install.
        f"        iifname \"wg0\" ip saddr @mitm_clients tcp dport 80 dnat ip to 127.0.0.1:8080",
        f"        iifname \"wg0\" ip saddr @mitm_clients tcp dport 443 dnat ip to 127.0.0.1:8080",
        "    }",
        "",
        "    chain forward {",
        "        type filter hook forward priority 0; policy accept;",
        # Blocked clients: HTTPS gets a fast TCP reset (browser fails in ms
        # instead of timing out); all other ports get dropped. We skip the
        # 443 reject for mitm-CA devices because their HTTPS is DNATed in
        # prerouting → mitmproxy, which serves a real block page (much nicer
        # UX than "Connection refused").
        "        iifname \"wg0\" ip saddr @blocked_clients ip saddr != @mitm_clients tcp dport 443 reject with tcp reset",
        "        iifname \"wg0\" ip saddr @blocked_clients tcp dport != 80 drop",
        "        iifname \"wg0\" ip saddr @blocked_clients meta l4proto udp drop",
        # Force devices with the CA installed off QUIC (HTTP/3) onto TCP/TLS,
        # so mitmproxy can actually intercept them. Browsers fall back to TCP
        # within ~1s when QUIC is unreachable. Without this, every YouTube /
        # Google / Cloudflare site bypasses our visibility entirely.
        "        iifname \"wg0\" ip saddr @mitm_clients udp dport 443 reject",
        # DNS-blocked domains (AdGuard sinkhole = 10.13.13.254): HTTPS gets
        # a fast TCP reset so the browser fails immediately instead of
        # hanging on a connect to nothing. HTTP path served by blockpage.
        "        iifname \"wg0\" ip daddr 10.13.13.254 tcp dport 443 reject with tcp reset",
        # DoH/DoT containment. Without this, a kid setting Chrome's "Use
        # secure DNS → Cloudflare" silently bypasses every filter. Reject
        # so the browser fails fast and falls back to system DNS (AdGuard).
        # MDM-enrolled devices already get DoH locked off at the OS layer;
        # this is the safety net for pre-enrolment / non-MDM clients.
        "        iifname \"wg0\" ip daddr @doh_dot_ips tcp dport { 443, 853 } reject with tcp reset",
        "        iifname \"wg0\" ip daddr @doh_dot_ips udp dport { 443, 853 } drop",
        "    }",
        "",
        "    chain postrouting {",
        "        type nat hook postrouting priority srcnat;",
        f"        ip saddr {WG_SUBNET} oifname != \"wg0\" masquerade",
        "    }",
        "}",
    ]
    return "\n".join(lines) + "\n"


def apply(ruleset: str) -> None:
    proc = subprocess.run(
        ["nft", "-f", "-"], input=ruleset, text=True, capture_output=True
    )
    if proc.returncode != 0:
        print(f"[gdlf-nft] nft failed:\n{proc.stderr}\n--- ruleset ---\n{ruleset}", file=sys.stderr)


def collect_blocked(cfg: dict, now: _dt.datetime) -> set[str]:
    """Same logic as render() — recomputed cheaply so we can diff cycles."""
    out: set[str] = set()
    for kid in cfg.get("kids", []) or []:
        kid_blocked = bool(kid.get("manual_block"))
        bonus_until = _parse_bonus(kid.get("bonus_until"))
        bonus_active = bonus_until is not None and bonus_until > now
        sched = (kid.get("schedule") or {})
        which = "weekend" if is_weekend(now.date()) else "weekday"
        allowed = (sched.get(which) or {}).get("allowed", "00:00-23:59")
        out_of_window = (not in_window(now, parse_windows(allowed))) and not bonus_active
        for d in kid.get("devices", []) or []:
            ip = d.get("wg_ip")
            if not ip:
                continue
            if kid_blocked or d.get("manual_block") or out_of_window:
                out.add(ip)
    return out


def flush_conntrack(ip: str) -> None:
    """Kill existing flows so a newly-blocked device loses its open streams
    immediately, not whenever conntrack idles them out. Without this, an
    already-loaded YouTube tab would keep playing for minutes after a block."""
    # Two passes: as source (kid → internet) and as the post-DNAT destination
    # (return packets entering loopback from mitm/blockpage targets).
    for flag in ("-s", "-d"):
        subprocess.run(
            ["conntrack", "-D", flag, ip],
            capture_output=True, text=True,
        )


def main() -> int:
    print(f"[gdlf-nft] starting. kids={KIDS_YAML} adguard={ADGUARD_IP} mitm={MITM_IP} interval={INTERVAL}s")
    last_hash = None
    last_blocked: set[str] = set()
    while True:
        try:
            cfg = load_kids()
            now = now_local()
            ruleset = render(cfg, now)
            blocked = collect_blocked(cfg, now)
            h = hash(ruleset)
            if h != last_hash:
                apply(ruleset)
                # Flush conntrack for IPs that newly entered the blocked set
                # — established TCP sessions otherwise dodge the new rules.
                newly_blocked = blocked - last_blocked
                for ip in newly_blocked:
                    flush_conntrack(ip)
                if newly_blocked:
                    print(f"[gdlf-nft] flushed conntrack for {sorted(newly_blocked)}")
                print(f"[gdlf-nft] reconciled at {now.isoformat()}")
                last_hash = h
                last_blocked = blocked
        except Exception as e:
            print(f"[gdlf-nft] error: {e}", file=sys.stderr)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main() or 0)
