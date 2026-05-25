from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    kids_yaml: Path
    state_dir: Path
    wg_easy_url: str
    adguard_url: str
    adguard_admin_password: str
    adguard_ui_port: int
    admin_password: str
    alert_webhook_url: str | None
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_pass: str | None
    smtp_from: str | None
    smtp_to: str | None
    wg_host: str
    wg_port: int
    wg_subnet: str
    tz: str
    # Retention: prune events older than this many days, and cap the table
    # at this many rows (whichever bites first). VACUUM runs on a much
    # slower cadence to reclaim disk space.
    retention_days: int
    max_events: int
    # Session-window size for activity-log dedup. Same-bucket repeats
    # (kid, host, path, query, decision) collapse into one row with
    # `hit_count` bumped; a 5-minute default means a 10-minute YouTube
    # session shows as ~2 rows with `×N`. Counter tiles also use this
    # bucket size for the sparkline grid.
    stats_bucket_secs: int
    # Kept for backwards compatibility with older `.env` files; no longer
    # consumed (the accumulator-based flush is gone). Safe to remove next
    # release.
    stats_retention_days: int
    stats_flush_secs: int
    # Public origin of the MDM endpoints; embedded in enrollment profiles.
    # e.g. "https://gdlf.cliftonc.nl:8443". Empty disables /mdm/* routes.
    mdm_base_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            kids_yaml=Path(os.environ.get("KIDS_YAML", "/etc/gdlf/kids.yaml")),
            state_dir=Path(os.environ.get("STATE_DIR", "/var/lib/gdlf")),
            wg_easy_url=os.environ.get("WG_EASY_URL", "http://wg-easy:51821"),
            adguard_url=os.environ.get("ADGUARD_URL", "http://adguard:80"),
            adguard_admin_password=os.environ.get("ADGUARD_ADMIN_PASSWORD", ""),
            adguard_ui_port=int(os.environ.get("ADGUARD_UI_PORT") or 8082),
            admin_password=os.environ.get("ADMIN_PASSWORD", ""),
            alert_webhook_url=os.environ.get("ALERT_WEBHOOK_URL") or None,
            smtp_host=os.environ.get("SMTP_HOST") or None,
            smtp_port=int(os.environ.get("SMTP_PORT") or 587),
            smtp_user=os.environ.get("SMTP_USER") or None,
            smtp_pass=os.environ.get("SMTP_PASS") or None,
            smtp_from=os.environ.get("SMTP_FROM") or None,
            smtp_to=os.environ.get("SMTP_TO") or None,
            wg_host=os.environ.get("WG_HOST", "vpn.example.com"),
            wg_port=int(os.environ.get("WG_PORT") or 51820),
            wg_subnet=os.environ.get("WG_SUBNET", "10.13.13.0/24"),
            tz=os.environ.get("TZ", "UTC"),
            retention_days=int(os.environ.get("RETENTION_DAYS") or 7),
            max_events=int(os.environ.get("MAX_EVENTS") or 200_000),
            stats_retention_days=int(os.environ.get("STATS_RETENTION_DAYS") or 7),
            stats_bucket_secs=int(os.environ.get("STATS_BUCKET_SECS") or 300),
            stats_flush_secs=int(os.environ.get("STATS_FLUSH_SECS") or 30),
            mdm_base_url=os.environ.get("MDM_BASE_URL", ""),
        )


settings = Settings.from_env()
