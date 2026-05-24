"""URL-rule evaluation. Shared by the dashboard ('test this rule') and
the mitmproxy addon (the actual enforcement point).

A rule's `match` is a host+path pattern:
    "youtube.com/shorts/*"        -> host=youtube.com, path starts with /shorts/
    "*.reddit.com/r/teenagers/*"  -> *.reddit.com matches subdomains
    "*/search"                    -> any host, path == /search
The match string is split on the FIRST slash. Host portion uses fnmatch;
path portion is a glob anchored at the start (trailing /* matches anything).

`query` is an optional regex (re.search) applied to the raw query string.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

from .schema import Kid, URLRule


@dataclass
class Decision:
    action: str  # 'allow' | 'block' | 'flag' | 'allow'  (default)
    rule: URLRule | None
    flag: bool


def _split_match(s: str) -> tuple[str, str]:
    if "/" in s:
        host, path = s.split("/", 1)
        return host, "/" + path
    return s, "/*"


def _host_matches(pattern: str, host: str) -> bool:
    host = host.lower()
    pattern = pattern.lower()
    if pattern == host:
        return True
    if fnmatch.fnmatchcase(host, pattern):
        return True
    # Bare "example.com" also matches subdomains "x.example.com"
    if "*" not in pattern and host.endswith("." + pattern):
        return True
    return False


def _path_matches(pattern: str, path: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern)


def match_rule(rule: URLRule, host: str, path: str, query: str | None) -> bool:
    hp, pp = _split_match(rule.match)
    if not _host_matches(hp, host):
        return False
    if not _path_matches(pp, path):
        return False
    if rule.query:
        # Treat a malformed regex as non-matching so a typo in kids.yaml
        # doesn't crash the decision API (which would then fail open).
        # Rule creation also validates up-front; this is defense in depth.
        try:
            if not re.search(rule.query, query or ""):
                return False
        except re.error:
            import logging
            logging.getLogger("gdlf.rules").warning(
                "rule has invalid query regex %r — skipping", rule.query
            )
            return False
    return True


def suggest_match(host: str, path: str) -> str:
    """Build a sensible match pattern from an observed host+path.

    Examples:
      youtube.com  /shorts/abc       -> youtube.com/shorts/*
      google.com   /search           -> google.com/search
      reddit.com   /r/teens/comments -> reddit.com/r/teens/*
      example.com  /                 -> example.com
    """
    host = (host or "").strip().lower()
    path = (path or "").strip() or "/"
    if path in ("", "/"):
        return host
    segs = [s for s in path.split("/") if s]
    if not segs:
        return host
    if len(segs) == 1:
        return f"{host}/{segs[0]}"
    take = segs[:2] if len(segs) > 2 else segs[:1]
    return f"{host}/{'/'.join(take)}/*"


def evaluate(kid: Kid, host: str, path: str = "/", query: str | None = None) -> Decision:
    """Walk the kid's url_rules top-to-bottom. First match wins.

    Returns Decision(action='allow') by default if nothing matches. The
    `flag` attribute is true if a matched rule had flag=true *or* if the
    rule's action itself is 'flag'.
    """
    for rule in kid.url_rules:
        if match_rule(rule, host, path, query):
            return Decision(
                action=rule.action,
                rule=rule,
                flag=rule.flag or rule.action == "flag",
            )
    return Decision(action="allow", rule=None, flag=False)


# Pretty block-page returned to the device on a hit.
BLOCK_PAGE_HTML = """\
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Blocked</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:-apple-system,BlinkMacSystemFont,system-ui,sans-serif;
      background:#0f1115;color:#e6e8ee;margin:0;
      display:flex;align-items:center;justify-content:center;min-height:100vh}
 .card{max-width:520px;padding:40px;text-align:center}
 h1{font-size:28px;margin:0 0 12px;color:#fff}
 p{margin:8px 0;line-height:1.5;color:#a8b0c0}
 code{background:#1d2230;padding:2px 6px;border-radius:4px;font-size:13px}
 .note{margin-top:24px;font-size:12px;color:#6b7186}
</style></head>
<body><div class="card">
  <h1>This page is blocked</h1>
  <p>The family network policy doesn't allow <code>{host}{path}</code>.</p>
  <p>If you think this is a mistake, ask a parent to check the rule:</p>
  <p><code>{rule_match}</code></p>
  <div class="note">gdlf • {kid_name}</div>
</div></body></html>
"""
