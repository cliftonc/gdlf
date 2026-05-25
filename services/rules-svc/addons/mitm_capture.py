"""gdlf mitmproxy addon.

For every request:
  1. Ask rules-svc what to do (decision API).
  2. If 'block': short-circuit with a synthetic 403 + block page.
  3. Either way, POST the event to rules-svc so it shows up in Activity.

The client IP is the kid's WG peer address — mitmproxy's transparent mode
preserves the original source IP, so this maps cleanly to a kid via kids.yaml.

For TLS connections we can't decrypt (the device hasn't installed the CA),
mitmproxy signals this as a `tls_failed` hook; we then log an SNI-only event
so the parent can at least see what hosts were reached.
"""
from __future__ import annotations

import asyncio
import base64
import fnmatch
import logging
import os
from pathlib import Path

import httpx
from mitmproxy import ctx, http
from mitmproxy.tls import ClientHelloData, TlsData


def _gandalf_data_url() -> str:
    p = Path(__file__).resolve().parent / "gandalf.png"
    if not p.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii")


GANDALF = _gandalf_data_url()

RULES_SVC_URL = os.environ.get("RULES_SVC_URL", "http://rules-svc:8080")
TIMEOUT = httpx.Timeout(2.0, connect=1.0)

# How often to refresh the per-IP passthrough host list from rules-svc.
PASSTHROUGH_REFRESH_SECS = 30

# Dedup is the server's job (see `db.insert_or_bump` and the
# `uq_event_session` unique index). The addon posts every observation as it
# happens — keeping the policy in one place is how we made the activity
# feed, the persistent list, and the counter tiles agree.


def _host_pattern_matches(pattern: str, host: str) -> bool:
    """Same matching semantics as `rules._host_matches` on the server.

    Bare `example.com` also matches `*.example.com` — without this,
    a parent typing `block youtube.com` would miss `www.youtube.com`
    and the rule would silently never fire.
    """
    pattern = (pattern or "").lower()
    host = (host or "").lower()
    if not pattern or not host:
        return False
    if pattern == host:
        return True
    if fnmatch.fnmatchcase(host, pattern):
        return True
    if "*" not in pattern and host.endswith("." + pattern):
        return True
    return False


# Bulk-CDN passthrough list is sourced from rules-svc at runtime (see
# `gdlf/bulk_cdns.py`). Mostly subsumed by splice-by-default now, but kept
# as a hard override for one release as belt-and-suspenders.
_BULK_CDN_FALLBACK: tuple[str, ...] = (
    "*.steamcontent.com",
    "*.windowsupdate.com",
    "*.dl.delivery.mp.microsoft.com",
)

# No inspect-list cold-start fallback: before the first /api/passthrough
# poll lands we splice everything, which is the safe default (no broken
# pinned apps). Once the poll arrives the per-kid inspect list takes over.

# TLS alert error strings that mean the client explicitly rejected our cert
# (i.e. real certificate pinning). Substring match against
# `TlsData.conn.error`, lower-cased. Anything else — network blip, cipher
# mismatch, client abort, mid-handshake disconnect — is treated as noise
# and not recorded.
#
# OpenSSL formats vary slightly across versions; the substrings here cover
# both OpenSSL 1.1.x and 3.x. If a new format shows up, log
# `data.conn.error` raw for 30 min and add the canonical substring.
_CERT_REJECT_SUBSTRINGS: tuple[str, ...] = (
    "alert unknown ca",          # TLS alert 48
    "alert bad certificate",      # TLS alert 42
    "alert certificate unknown",  # TLS alert 46
    "alert certificate required", # TLS alert 116
    "alert access denied",        # TLS alert 49
)


# Sec-Fetch-Dest values that map to "asset" (sub-resource, not a navigation).
# Modern browsers (Chrome / Firefox / Safari / Edge / Android Chrome) set
# this on every request. See:
# https://developer.mozilla.org/docs/Web/HTTP/Headers/Sec-Fetch-Dest
_SEC_FETCH_ASSET = {
    "image", "audio", "video", "track", "font",
    "script", "style", "manifest", "worker", "sharedworker", "serviceworker",
    "embed", "object",
    # Beacons / pings / fonts — definitely not page navigations.
    "report", "paintworklet", "audioworklet",
}
_SEC_FETCH_PAGE = {"document", "iframe", "frame", "nested-document"}

# Accept-header fallback for clients that don't set Sec-Fetch-Dest.
def _from_accept(accept: str) -> str | None:
    accept = (accept or "").lower()
    # text/html appears in Chrome's Accept for navigation but ALSO in fetch()
    # if the caller is lazy. Only treat as page if text/html is the FIRST
    # preference (real navigations almost always start with it).
    primary = accept.split(",", 1)[0].strip()
    if primary.startswith("text/html"):
        return "page"
    for prefix in ("image/", "audio/", "video/", "font/"):
        if prefix in accept:
            return "asset"
    if "application/javascript" in accept or "text/css" in accept:
        return "asset"
    # JSON / GraphQL / gRPC-web / protobuf — almost always XHR / fetch.
    for marker in (
        "application/json", "application/graphql", "application/grpc",
        "application/x-protobuf", "application/vnd.api+json", "+json",
    ):
        if marker in accept:
            return "xhr"
    return None


# Last-resort URL-extension fallback for ancient clients / native apps.
_ASSET_EXTS = {
    ".js", ".mjs", ".css", ".map",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".avif",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp4", ".webm", ".m4s", ".ts", ".m3u8", ".mpd",
    ".mp3", ".aac", ".ogg", ".flac", ".wav",
}


def _classify(req) -> str:
    """Return 'page' | 'iframe' | 'asset' | 'xhr' | 'ws' for the given request.

    Order of confidence:
      1. WebSocket upgrade (Connection: upgrade + Upgrade: websocket)
      2. Sec-Fetch-Dest (browser's explicit declaration)
      3. Request body content-type / accept header
      4. Method (non-GET is almost never a navigation)
      5. URL extension
    Default 'page' only when every signal is silent.
    """
    h = req.headers
    # WebSocket upgrade — very common in SPA / realtime apps (Reddit's
    # gql-realtime.reddit.com/query is exactly this).
    if "websocket" in (h.get("Upgrade") or "").lower():
        return "ws"
    dest = (h.get("Sec-Fetch-Dest") or "").lower()
    if dest == "websocket":
        return "ws"
    if dest == "document":
        return "page"
    if dest in ("iframe", "frame", "nested-document"):
        return "iframe"
    if dest in _SEC_FETCH_ASSET:
        return "asset"
    if dest == "empty":
        return "xhr"

    # The request body itself tells us a lot: JSON/protobuf/form bodies are
    # XHR/POSTs, never page loads.
    ctype = (h.get("Content-Type") or "").lower()
    if any(m in ctype for m in (
        "application/json", "application/graphql", "application/grpc",
        "application/x-protobuf", "application/x-www-form-urlencoded",
        "multipart/form-data",
    )):
        return "xhr"

    guess = _from_accept(h.get("Accept", ""))
    if guess:
        return guess

    # Non-GET methods are almost never page navigations.
    if req.method.upper() not in ("GET", "HEAD"):
        return "xhr"

    # URL extension fallback.
    path = req.path.split("?", 1)[0]
    last = path.rsplit("/", 1)[-1].lower()
    if "." in last:
        ext = "." + last.rsplit(".", 1)[-1]
        if ext in _ASSET_EXTS:
            return "asset"
    return "page"

# Static HTML — no format placeholders, no KeyError risk. The Gandalf
# image is inlined as a data: URL so the block page renders without any
# extra network requests (we're on a blocked URL — there's no other
# origin the device can reach).
BLOCK_PAGE = ("""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Blocked</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:-apple-system,BlinkMacSystemFont,system-ui,sans-serif;
      background:#0f1115;color:#e6e8ee;margin:0;
      display:flex;align-items:center;justify-content:center;min-height:100vh}
 .card{max-width:720px;padding:40px;text-align:center}
 .card img{width:min(440px,80vw);height:auto;margin:0 auto 20px;display:block;
           filter:drop-shadow(0 6px 32px rgba(0,0,0,0.45))}
 h1{font-size:28px;margin:0 0 12px;color:#fff}
 p{margin:8px 0;line-height:1.5;color:#a8b0c0}
 .note{margin-top:24px;font-size:12px;color:#6b7186}
</style></head>
<body><div class="card">
  <img src="__GANDALF__" alt="">
  <h1>You shall not pass</h1>
  <p>Ask a parent if you think this is a mistake.</p>
  <div class="note">gdlf</div>
</div></body></html>""").replace("__GANDALF__", GANDALF).encode("utf-8")


class GdlfAddon:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=TIMEOUT)
        self._log = logging.getLogger("gdlf")
        # ip -> [glob, ...]; refreshed by _passthrough_refresh_loop.
        # Legacy hard-override passthrough list (kept one release).
        self._passthrough: dict[str, list[str]] = {}
        # ip -> [glob, ...]; the per-kid inspect list. Under splice-by-default,
        # any SNI NOT in this list gets `ignore_connection = True` in
        # `tls_clienthello`. Empty / unknown-IP = splice everything, which is
        # the safe cold-start behaviour (no pinned-app breakage).
        self._inspect: dict[str, list[str]] = {}
        # Bulk-CDN globs — same refresh loop, applies to all clients.
        # Legacy: redundant under splice-by-default but kept as hard override.
        self._bulk_cdn: tuple[str, ...] = _BULK_CDN_FALLBACK
        # Client IPs that are currently network-blocked (schedule / manual).
        # We refuse to splice TLS for these — falling through to MITM lets
        # /api/decision serve the block page. Without this, splice silently
        # bypasses schedule enforcement (nft's :443 reject exempts mitm clients
        # and trusts mitmproxy to enforce policy at the decision layer).
        self._blocked_ips: frozenset[str] = frozenset()
        self._passthrough_task: asyncio.Task | None = None

    def running(self) -> None:
        """mitmproxy lifecycle hook — kick off the passthrough refresh task
        on the running event loop. Can't do this in __init__ because the
        event loop isn't running yet at addon-import time."""
        if self._passthrough_task is None:
            self._passthrough_task = asyncio.create_task(self._passthrough_refresh_loop())

    async def _passthrough_refresh_loop(self) -> None:
        while True:
            try:
                r = await self._client.get(f"{RULES_SVC_URL}/api/passthrough")
                r.raise_for_status()
                body = r.json() or {}
                by_ip = body.get("by_ip") or {}
                self._passthrough = {
                    ip: [str(h).lower() for h in hosts]
                    for ip, hosts in by_ip.items()
                }
                inspect = body.get("inspect_by_ip") or {}
                self._inspect = {
                    ip: [str(h).lower() for h in hosts]
                    for ip, hosts in inspect.items()
                }
                globs = body.get("globals")
                if isinstance(globs, list) and globs:
                    self._bulk_cdn = tuple(str(g).lower() for g in globs)
                blocked = body.get("blocked_ips")
                if isinstance(blocked, list):
                    self._blocked_ips = frozenset(str(ip) for ip in blocked)
            except Exception as e:
                self._log.debug("passthrough refresh failed: %s", e)
            await asyncio.sleep(PASSTHROUGH_REFRESH_SECS)

    def _inspect_match(self, client_ip: str, sni: str) -> bool:
        """True if `sni` is in the per-kid inspect list. Inspect = MITM =
        decrypt. Unknown IPs and the cold-start window return False (i.e.
        splice everything), which is the safe default.

        Matching semantics mirror `rules._host_matches` so the parent's
        intuition is the same in both places:
          * exact match
          * fnmatch glob (`*.example.com` etc.)
          * bare host matches subdomains (`youtube.com` -> `m.youtube.com`)
        """
        sni = (sni or "").lower()
        if not sni:
            return False
        for pat in self._inspect.get(client_ip, ()):
            if _host_pattern_matches(pat, sni):
                return True
        return False

    def _is_bulk_cdn(self, host: str) -> bool:
        return any(_host_pattern_matches(p, host) for p in self._bulk_cdn)

    def _passthrough_match(self, client_ip: str, sni: str) -> str | None:
        """Return the matched glob if `sni` should be passed through for
        `client_ip`, else None."""
        sni = (sni or "").lower()
        if not sni:
            return None
        for pat in self._passthrough.get(client_ip, ()):
            if _host_pattern_matches(pat, sni):
                return pat
        return None

    async def _decision(self, client_ip: str, host: str, path: str, query: str | None) -> dict:
        try:
            r = await self._client.post(
                f"{RULES_SVC_URL}/api/decision",
                json={
                    "client_ip": client_ip,
                    "host": host,
                    "path": path,
                    "query": query or "",
                },
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            self._log.warning("decision lookup failed: %s", e)
            # Fail open — don't lock the kid out if rules-svc is down.
            return {"action": "allow", "kid": None, "rule": None, "flag": False}

    def _post_event(self, payload: dict) -> None:
        # Fire-and-forget; never block the user's request on this.
        asyncio.create_task(self._send_event(payload))

    async def _send_event(self, payload: dict) -> None:
        try:
            await self._client.post(f"{RULES_SVC_URL}/api/events", json=payload)
        except Exception as e:
            self._log.debug("event post failed: %s", e)

    async def request(self, flow: http.HTTPFlow) -> None:
        req = flow.request
        client_ip = flow.client_conn.peername[0] if flow.client_conn.peername else ""
        host = req.pretty_host
        path = req.path.split("?", 1)[0] if "?" in req.path else req.path
        # Always pass a string-or-None — earlier we relied on `req.query.fields`
        # truthiness, but empty-fields returns an empty *list* which (after JSON
        # round-trip) violated the DB's TEXT column and crashed the event insert.
        query = req.url.split("?", 1)[1] if "?" in req.url else None

        decision = await self._decision(client_ip, host, path, query)
        action = decision.get("action", "allow")
        rule = decision.get("rule")
        kid = decision.get("kid")
        flag = bool(decision.get("flag"))

        if action == "block":
            flow.response = http.Response.make(
                403, BLOCK_PAGE, {"Content-Type": "text/html; charset=utf-8"},
            )

        # Send every event to rules-svc — the server applies the single
        # ingest rule (page/iframe/unknown navigations + any
        # block/flag/tls_failed/dns_block + SNI splices, deduplicated
        # within a session window). The addon doesn't filter or dedup
        # locally; keeping all the policy server-side is what makes the
        # feed, the persistent list, and the counter tiles agree.
        self._post_event(
            {
                "client_ip": client_ip,
                "method": req.method,
                "host": host,
                "path": path,
                "query": query,
                "decision": action if action in {"allow", "block", "flag"} else "allow",
                "rule": rule,
                "flag": flag,
                "sni_only": False,
                "status": 403 if action == "block" else None,
                "kind": _classify(req),
            }
        )

    async def response(self, flow: http.HTTPFlow) -> None:
        # We log at request time; nothing extra to do here for now.
        pass

    def tls_clienthello(self, data: ClientHelloData) -> None:
        """Splice-by-default: every connection skips MITM unless its SNI
        appears in the per-kid inspect list (or the global defaults).

        Rationale: full MITM of every flow breaks pinned-cert apps and
        forced us to maintain a runaway passthrough list. Path-level rules
        only meaningfully apply to a handful of domains (YouTube etc.);
        for everything else DNS + SNI give enough granularity at the
        domain level. See `gdlf.bulk_cdns.INSPECT_GLOBAL_DEFAULTS`.
        """
        try:
            sni = data.client_hello.sni or ""
            client_ip = (
                data.context.client.peername[0]
                if data.context.client.peername
                else ""
            )
        except Exception:
            return
        if not sni or not client_ip:
            return
        # Network-blocked clients fall through to MITM so /api/decision can
        # serve the block page. nft's :443 reject deliberately exempts mitm
        # clients on the assumption we'll enforce policy here.
        if client_ip in self._blocked_ips:
            return
        # Inspect list wins: terminate TLS so the URL-rule path can run.
        if self._inspect_match(client_ip, sni):
            return
        # Otherwise splice. Whether the match came via the explicit legacy
        # passthrough lists or the new default, the outcome is identical:
        # mitmproxy tunnels the TLS bytes untouched. Keep the legacy match
        # only to log a `rule` string for parity with prior dashboard rows.
        matched = (
            "bulk-cdn" if self._is_bulk_cdn(sni)
            else (self._passthrough_match(client_ip, sni) or "splice-default")
        )
        data.ignore_connection = True
        # Activity emit: we can't see inside the encrypted stream, but we
        # know this kid hit this SNI. The server upserts these into the
        # `event` table (one row per (kid, host) per session window),
        # which keeps the feed readable even though the browser opens
        # many connections per page-load.
        self._post_event(
            {
                "client_ip": client_ip,
                "method": None,
                "host": sni,
                "path": None,
                "query": None,
                "decision": "passthrough",
                "rule": matched,
                "flag": False,
                "sni_only": True,
                "status": None,
                # `sni` = we only saw the SNI (TLS spliced, no decryption).
                # Differentiates from `page` / `iframe` (decrypted nav rows).
                "kind": "sni",
            }
        )

    def tls_failed_client(self, data: TlsData) -> None:
        """Record actual cert-pinning rejections against an inspect-listed
        host. Under splice-by-default this should be rare — splice never
        presents a cert so there's nothing for the client to reject. Still
        fires for SNIs in the inspect list whose apps pin their certs.

        We filter on `data.conn.error`: only TLS-alert strings that
        unambiguously mean "client refused our cert" are recorded. Network
        blips, cipher mismatches, and client aborts are dropped — that
        filter is what fixed the runaway-passthrough bug.

        Posts a single event to /api/events with decision=tls_failed.
        The server deduplicates same-bucket repeats automatically, so
        a pinning app that retries 50 times in 5 min shows as one row
        with `hit_count=50` instead of spamming the feed.
        """
        try:
            host = data.context.client.sni or ""
            client_ip = data.context.client.peername[0] if data.context.client.peername else ""
            err = (getattr(data.conn, "error", "") or "").lower()
        except Exception:
            return
        if not host:
            return
        if not any(s in err for s in _CERT_REJECT_SUBSTRINGS):
            return
        self._post_event(
            {
                "client_ip": client_ip,
                "method": None,
                "host": host,
                "path": None,
                "query": None,
                "decision": "tls_failed",
                "rule": None,
                "flag": False,
                "sni_only": True,
                "status": None,
                # `pinned` = MITM-listed host whose app rejected our cert.
                "kind": "pinned",
                "note": err,
            }
        )


addons = [GdlfAddon()]
