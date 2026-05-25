"""Curated catalog of mobile browsers gdlf knows about.

Single source for iOS bundle IDs / Android package names. The MDM
payload builders (`mdm.profiles`, `amapi.policy`) consume this to
derive the blocklist from a `BrowserPolicy`, and the dashboard renders
the catalog as a dropdown so a parent can pick the allowed browser
without typing IDs.

Adding a new browser: add a `BrowserEntry` below. If it should also be
selectable as the *allowed* browser, add its key to the matching
`IosBrowser` / `AndroidBrowser` Literal in `schema.py`.
"""
from __future__ import annotations

from dataclasses import dataclass

from .schema import (
    AndroidBrowserPolicy,
    ChromeManagedConfig,
    IosBrowserPolicy,
)


# Chromium-based browsers honour the same managed-app-config schema
# (IncognitoModeAvailability / SyncDisabled / BrowserSignin /
# SearchSuggestEnabled). Firefox uses a different policy surface; Safari
# has no AppConfig at all.
CHROMIUM_BROWSER_KEYS: frozenset[str] = frozenset({"chrome", "edge", "brave"})


@dataclass(frozen=True)
class BrowserEntry:
    key: str                                  # matches the schema Literal value
    label: str                                # for the UI dropdown
    ios_bundle_ids: tuple[str, ...]           # empty if not on iOS
    android_packages: tuple[str, ...]         # empty if not on Android


# Bundle IDs / package names sourced from public App Store + Play
# listings. Spot-check before merging when a major browser ships a
# rebrand — Firefox iOS migrated from `org.mozilla.firefox` to
# `org.mozilla.ios.Firefox` years ago and similar moves happen.
CATALOG: tuple[BrowserEntry, ...] = (
    BrowserEntry(
        "chrome", "Chrome",
        ("com.google.chrome.ios",),
        ("com.android.chrome",),
    ),
    BrowserEntry(
        "firefox", "Firefox",
        ("org.mozilla.ios.Firefox", "org.mozilla.ios.Focus"),
        (
            "org.mozilla.firefox",
            "org.mozilla.firefox.beta",
            "org.mozilla.focus",
            "org.mozilla.klar",
        ),
    ),
    BrowserEntry(
        "edge", "Edge",
        ("com.microsoft.msedge",),
        ("com.microsoft.emmx",),
    ),
    BrowserEntry(
        "brave", "Brave",
        ("com.brave.ios.browser",),
        ("com.brave.browser",),
    ),
    BrowserEntry(
        "samsung_internet", "Samsung Internet",
        (),
        ("com.sec.android.app.sbrowser",),
    ),
    BrowserEntry(
        "opera", "Opera",
        ("com.opera.OperaTouch", "com.opera.mini.native"),
        ("com.opera.browser", "com.opera.mini.native", "com.opera.gx"),
    ),
    BrowserEntry(
        "duckduckgo", "DuckDuckGo",
        ("com.duckduckgo.mobile.ios",),
        ("com.duckduckgo.mobile.android",),
    ),
    BrowserEntry(
        "yandex", "Yandex",
        ("ru.yandex.mobile.search",),
        ("com.yandex.browser",),
    ),
    BrowserEntry("vivaldi", "Vivaldi", (), ("com.vivaldi.browser",)),
    BrowserEntry("tor", "Tor", (), ("org.torproject.torbrowser",)),
    BrowserEntry("aloha", "Aloha", ("com.aloha.browser",), ()),
    BrowserEntry(
        "onion", "Onion Browser",
        ("com.tigaslabs.OnionBrowser",),
        (),
    ),
    BrowserEntry("kiwi", "Kiwi", (), ("com.kiwibrowser.browser",)),
)


_BY_KEY: dict[str, BrowserEntry] = {b.key: b for b in CATALOG}


def entry(key: str) -> BrowserEntry | None:
    return _BY_KEY.get(key)


def ios_allowed_bundle_id(policy: IosBrowserPolicy) -> str | None:
    """The primary bundle ID of the allowed Chromium-based iOS browser, or
    None if the allowed browser is `safari` (handled via `allowSafari`),
    `firefox` (no Chromium AppConfig), or `none`."""
    if policy.allowed_browser not in CHROMIUM_BROWSER_KEYS:
        return None
    e = _BY_KEY.get(policy.allowed_browser)
    return e.ios_bundle_ids[0] if e and e.ios_bundle_ids else None


def android_allowed_package(policy: AndroidBrowserPolicy) -> str | None:
    """The primary package of the allowed Android browser, or None if
    `none`. Force-installed via AMAPI applications[] when set."""
    if policy.allowed_browser == "none":
        return None
    e = _BY_KEY.get(policy.allowed_browser)
    return e.android_packages[0] if e and e.android_packages else None


def ios_blocklist(policy: IosBrowserPolicy) -> list[str]:
    """Curated bundle IDs ∪ extra_blocked, minus the allowed browser's
    IDs, minus the unblocked override. Sorted for stable plist output."""
    blocked: set[str] = set()
    allowed = _BY_KEY.get(policy.allowed_browser)
    allowed_ids: set[str] = set(allowed.ios_bundle_ids) if allowed else set()
    for b in CATALOG:
        blocked.update(b.ios_bundle_ids)
    blocked.update(policy.extra_blocked)
    blocked -= allowed_ids
    blocked -= set(policy.unblocked)
    return sorted(blocked)


def android_blocklist(policy: AndroidBrowserPolicy) -> list[str]:
    """Same shape as ios_blocklist for Android package names."""
    blocked: set[str] = set()
    allowed = _BY_KEY.get(policy.allowed_browser)
    allowed_pkgs: set[str] = set(allowed.android_packages) if allowed else set()
    for b in CATALOG:
        blocked.update(b.android_packages)
    blocked.update(policy.extra_blocked)
    blocked -= allowed_pkgs
    blocked -= set(policy.unblocked)
    return sorted(blocked)


def chrome_cfg_dict(cfg: ChromeManagedConfig) -> dict:
    """Map our boolean toggles onto Chromium's enterprise policy keys.

    IncognitoModeAvailability: 0=enabled, 1=disabled, 2=forced.
    BrowserSignin:              0=disabled, 1=enabled, 2=forced.
    """
    return {
        "IncognitoModeAvailability": 1 if cfg.incognito_disabled else 0,
        "SyncDisabled": cfg.sync_disabled,
        "BrowserSignin": 0 if cfg.signin_disabled else 1,
        "SearchSuggestEnabled": cfg.search_suggest_enabled,
    }


def catalog_for_api() -> list[dict]:
    """JSON-serialisable view of the catalog for the dashboard dropdown.
    Each entry carries ios/android flags so the UI can filter per
    platform."""
    return [
        {
            "key": b.key,
            "label": b.label,
            "ios_supported": bool(b.ios_bundle_ids),
            "android_supported": bool(b.android_packages),
        }
        for b in CATALOG
    ]
