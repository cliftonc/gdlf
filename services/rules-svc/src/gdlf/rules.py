"""URL-rule evaluation. Shared by the dashboard ('test this rule') and
the mitmproxy addon (the actual enforcement point).

Rules have separate `host` and `path` fields. Path + query are MITM-only
filters: without decryption we can't see them, so for a non-MITM host the
rule degrades to a domain-only match. `host_matches_inspect()` is the
predicate the addon uses to decide if a SNI is effectively MITM'd.

Examples:
    host="youtube.com", path="/shorts/*"
        kid has youtube.com in MITM → blocks only /shorts/*
        kid does NOT have it in MITM → matches the host alone (whole site)

    host="*.reddit.com" (no path)
        Always a domain-only match.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

from .schema import Kid, URLRule


@dataclass
class Decision:
    action: str  # 'allow' | 'block' | 'flag'
    rule: URLRule | None
    flag: bool


def _host_matches(pattern: str, host: str) -> bool:
    host = (host or "").lower()
    pattern = (pattern or "").lower()
    if not pattern:
        return False
    if pattern == host:
        return True
    if fnmatch.fnmatchcase(host, pattern):
        return True
    # Bare "example.com" also matches subdomains "x.example.com"
    if "*" not in pattern and host.endswith("." + pattern):
        return True
    return False


def _path_matches(pattern: str | None, path: str) -> bool:
    if not pattern:
        return True  # no path on the rule = host-only match
    p = pattern if pattern.startswith("/") else "/" + pattern
    return fnmatch.fnmatchcase(path or "/", p)


def host_in_inspect(kid: Kid, host: str) -> bool:
    """True if `host` matches any of the kid's MITM globs. Used both by the
    evaluator (to decide whether path/query filters apply) and by the
    addon's SNI-time decision logic."""
    for pat in kid.mitm_inspect_hosts:
        if _host_matches(pat, host):
            return True
    return False


def match_rule(
    rule: URLRule,
    host: str,
    path: str,
    query: str | None,
    *,
    host_is_mitm: bool,
) -> bool:
    if not _host_matches(rule.host, host):
        return False
    # Path + query are MITM-only filters. Without decryption we have no
    # path to compare against; a rule that specified one degrades to a
    # host-only match — coarser than the parent typed but at least the
    # rule still bites.
    if host_is_mitm:
        if rule.path and not _path_matches(rule.path, path):
            return False
        if rule.query:
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


def suggest_match(host: str, path: str) -> dict[str, str | None]:
    """Build sensible host + path globs from an observed host + path.

    Returns {"host": ..., "path": ...|None}.

    Examples:
      youtube.com  /shorts/abc       -> host=youtube.com path=/shorts/*
      google.com   /search           -> host=google.com  path=/search
      reddit.com   /r/teens/comments -> host=reddit.com  path=/r/teens/*
      example.com  /                 -> host=example.com path=None
    """
    host = (host or "").strip().lower()
    path = (path or "").strip()
    if not path or path == "/":
        return {"host": host, "path": None}
    segs = [s for s in path.split("/") if s]
    if not segs:
        return {"host": host, "path": None}
    if len(segs) == 1:
        return {"host": host, "path": f"/{segs[0]}"}
    take = segs[:2] if len(segs) > 2 else segs[:1]
    return {"host": host, "path": f"/{'/'.join(take)}/*"}


def effective_inspect_hosts(kid: Kid) -> list[str]:
    """Hosts the addon will actually decrypt for this kid."""
    return sorted({*kid.mitm_inspect_hosts, *hosts_with_block_or_flag_rules(kid)})


def effective_host_in_inspect(kid: Kid, host: str) -> bool:
    """True when `host` matches the effective addon inspect list."""
    for pat in effective_inspect_hosts(kid):
        if _host_matches(pat, host):
            return True
    return False


def evaluate(kid: Kid, host: str, path: str = "/", query: str | None = None) -> Decision:
    """Walk the kid's url_rules top-to-bottom. First match wins.

    Returns Decision(action='allow') by default if nothing matches. The
    `flag` attribute is true if a matched rule had flag=true *or* if the
    rule's action itself is 'flag'.

    The MITM-status of `host` mirrors the addon's effective inspect list:
    explicit inspect hosts plus hosts from block/flag rules. For non-MITM
    hosts the evaluator silently drops any path/query predicates, so the
    rule degrades to a host-only match.
    """
    host_is_mitm = effective_host_in_inspect(kid, host)
    for rule in kid.url_rules:
        if match_rule(rule, host, path, query, host_is_mitm=host_is_mitm):
            return Decision(
                action=rule.action,
                rule=rule,
                flag=rule.flag or rule.action == "flag",
            )
    return Decision(action="allow", rule=None, flag=False)


def hosts_with_block_or_flag_rules(kid: Kid) -> list[str]:
    """Return the host globs of every block/flag rule on this kid.

    The addon unions this with `mitm_inspect_hosts` so domain-only block
    rules still bite even when the parent hasn't explicitly added the host
    to MITM. Without this, /api/decision would never be called for the
    domain and the rule would silently never fire.
    """
    return [r.host for r in kid.url_rules if r.action in ("block", "flag") or r.flag]


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
