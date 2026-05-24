"""Tiny block-page server. Listens on two ports so we can show a different
explanation for each kind of block:

  :8888  schedule block ("Outside allowed hours")
         nft routes here when source IP is in the schedule's blocked set.
  :8889  DNS-sinkhole block ("Site blocked by family filter")
         nft routes here when destination IP is 10.13.13.254 (the sinkhole
         IP AdGuard returns for blocklisted domains). The Host header
         tells us *which* site was blocked.

Both servers return the same HTML for any path/method. No deps beyond stdlib.
"""
from __future__ import annotations

import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _gandalf_data_url() -> str:
    p = Path(__file__).resolve().parent / "gandalf.png"
    if not p.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii")


GANDALF = _gandalf_data_url()


_CARD_STYLE = """
 body{font-family:-apple-system,BlinkMacSystemFont,system-ui,sans-serif;
      background:#0f1115;color:#e6e8ee;margin:0;
      display:flex;align-items:center;justify-content:center;min-height:100vh}
 .card{max-width:720px;padding:40px;text-align:center}
 .card img{width:min(440px,80vw);height:auto;margin:0 auto 20px;display:block;
           filter:drop-shadow(0 6px 32px rgba(0,0,0,0.45))}
 h1{font-size:32px;margin:0 0 12px;color:#fff}
 p{margin:8px 0;line-height:1.5;color:#a8b0c0}
 code{background:#1d2230;padding:2px 6px;border-radius:4px;font-size:13px}
 .note{margin-top:24px;font-size:12px;color:#6b7186}
"""


SCHEDULE_HTML = (
    f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Outside allowed hours</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{_CARD_STYLE}</style></head>
<body><div class="card">
  <img src="{GANDALF}" alt="">
  <h1>You shall not pass</h1>
  <p>This device's internet is paused by the family schedule.</p>
  <p>It will come back on at the next allowed window.</p>
  <div class="note">gdlf</div>
</div></body></html>
"""
).encode("utf-8")

# Sinkhole page is *templated* (we substitute the Host header into it).
# Brace-escape every CSS `{` / `}` because we run .format() on this.
SINKHOLE_TEMPLATE = (
    "<!doctype html>"
    "<html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<title>Blocked</title>"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<style>" + _CARD_STYLE.replace("{", "{{").replace("}", "}}") + "</style></head>"
    "<body><div class=\"card\">"
    f"<img src=\"{GANDALF}\" alt=\"\">"
    "<h1>You shall not pass</h1>"
    "<p>The family DNS filter doesn't allow <code>{host}</code>.</p>"
    "<p>If you think this is a mistake, ask a parent to review the blocklist.</p>"
    "<div class=\"note\">gdlf</div>"
    "</div></body></html>"
)


def _make_handler(static_html: bytes | None):
    """Build a handler. If static_html is None, render the sinkhole template
    using the request's Host header."""

    class Handler(BaseHTTPRequestHandler):
        def _body(self) -> bytes:
            if static_html is not None:
                return static_html
            host = self.headers.get("Host", "(unknown)").split(":", 1)[0]
            # Defang against HTML injection in the Host header.
            safe = host.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
            return SINKHOLE_TEMPLATE.format(host=safe).encode("utf-8")

        def _send(self):
            body = self._body()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):    self._send()
        def do_POST(self):   self._send()
        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()

        def log_message(self, fmt, *args):  # quiet
            return

    return Handler


def _serve(port: int, handler) -> None:
    srv = ThreadingHTTPServer(("0.0.0.0", port), handler)
    print(f"[blockpage] listening on :{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    threads = [
        threading.Thread(target=_serve, args=(8888, _make_handler(SCHEDULE_HTML)), daemon=True),
        threading.Thread(target=_serve, args=(8889, _make_handler(None)), daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
