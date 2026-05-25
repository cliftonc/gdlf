import { Icon } from "@iconify/react";
import type { Platform } from "../lib/schemas";

// Brand glyphs (Material Design Icons via Iconify) — recognisable platform
// logos rather than generic device shapes. Iconify lazy-loads from its CDN
// with browser-side caching, so the bundle stays tiny.
const ICON: Record<Platform, string> = {
  ios: "mdi:apple-ios",
  macos: "mdi:apple",
  android: "mdi:android",
  chromeos: "mdi:google-chrome",
  windows: "mdi:microsoft-windows",
  linux: "mdi:linux",
  other: "mdi:devices",
};

const LABEL: Record<Platform, string> = {
  ios: "iOS",
  macos: "macOS",
  android: "Android",
  chromeos: "ChromeOS",
  windows: "Windows",
  linux: "Linux",
  other: "Other",
};

export function PlatformIcon({
  platform,
  className = "",
}: {
  platform: Platform;
  className?: string;
}) {
  return (
    <span title={LABEL[platform]} aria-label={LABEL[platform]} className="inline-flex">
      <Icon icon={ICON[platform]} className={className} />
    </span>
  );
}

export function platformLabel(p: Platform): string {
  return LABEL[p];
}
