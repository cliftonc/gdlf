import { z } from "zod";

export const PlatformEnum = z.enum([
  "ios",
  "android",
  "chromeos",
  "windows",
  "macos",
  "linux",
  "other",
]);
export type Platform = z.infer<typeof PlatformEnum>;

export const RuleActionEnum = z.enum(["block", "allow", "flag"]);
export type RuleAction = z.infer<typeof RuleActionEnum>;

export const MdmStatusEnum = z.enum(["pending", "enrolled", "checked_out"]);
export type MdmStatus = z.infer<typeof MdmStatusEnum>;

export const MdmStateSchema = z.object({
  status: MdmStatusEnum,
  udid: z.string().nullable(),
  supervised: z.boolean(),
  enrolled_at: z.string().nullable(),
  last_checkin_at: z.string().nullable(),
});
export type MdmState = z.infer<typeof MdmStateSchema>;

export const AndroidMdmStatusEnum = z.enum(["pending", "active", "disabled", "deleted"]);
export type AndroidMdmStatus = z.infer<typeof AndroidMdmStatusEnum>;

export const AndroidMdmStateSchema = z.object({
  status: AndroidMdmStatusEnum,
  model: z.string().nullable(),
  enrolled_at: z.string().nullable(),
  last_status_at: z.string().nullable(),
  applied_policy_version: z.string().nullable(),
  device_name: z.string().nullable(),
});
export type AndroidMdmState = z.infer<typeof AndroidMdmStateSchema>;

export const WindowsMdmStatusEnum = z.enum(["pending", "enrolled", "revoked"]);
export type WindowsMdmStatus = z.infer<typeof WindowsMdmStatusEnum>;

export const WindowsMdmStateSchema = z.object({
  status: WindowsMdmStatusEnum,
  package_id: z.string(),
  package_version: z.string(),
  enrolled_at: z.string().nullable(),
  last_built_at: z.string().nullable(),
});
export type WindowsMdmState = z.infer<typeof WindowsMdmStateSchema>;

export const DeviceSchema = z.object({
  name: z.string(),
  platform: PlatformEnum,
  wg_ip: z.string(),
  wg_public_key: z.string().nullable(),
  mitm_ca_installed: z.boolean(),
  manual_block: z.boolean(),
  last_handshake: z.number(),
  rx: z.number(),
  tx: z.number(),
  online: z.boolean(),
  // .nullish() accepts both null and undefined, so a stale cached JSON
  // payload (pre-Phase-5 backend) doesn't break the dashboard.
  mdm: MdmStateSchema.nullish(),
  android_mdm: AndroidMdmStateSchema.nullish(),
  windows_mdm: WindowsMdmStateSchema.nullish(),
});
export type Device = z.infer<typeof DeviceSchema>;

export const MdmEnrollTokenResponseSchema = z.object({
  token: z.string(),
  enroll_url: z.string(),
  expires_at: z.string(),
});
export type MdmEnrollTokenResponse = z.infer<typeof MdmEnrollTokenResponseSchema>;

export const MdmCommandRowSchema = z.object({
  command_uuid: z.string(),
  request_type: z.string(),
  status: z.string(),
  created_at: z.string(),
  sent_at: z.string().nullable(),
  completed_at: z.string().nullable(),
});
export type MdmCommandRow = z.infer<typeof MdmCommandRowSchema>;

export const MdmResponseRowSchema = z.object({
  command_uuid: z.string(),
  status: z.string(),
  ts: z.string(),
  response_excerpt: z.string(),
});
export type MdmResponseRow = z.infer<typeof MdmResponseRowSchema>;

export const MdmCommandsSchema = z.object({
  queue: z.array(MdmCommandRowSchema),
  responses: z.array(MdmResponseRowSchema),
});
export type MdmCommands = z.infer<typeof MdmCommandsSchema>;

export const RuleSchema = z.object({
  action: RuleActionEnum,
  host: z.string(),
  // Path is MITM-only: enforced only when the host is on the kid's MITM
  // list. For non-MITM hosts the rule degrades to a host-only match.
  path: z.string().nullable().default(null),
  query: z.string().nullable(),
  flag: z.boolean(),
  note: z.string().nullable(),
});
export type Rule = z.infer<typeof RuleSchema>;

export const ScheduleSchema = z.object({
  weekday: z.string(),
  weekend: z.string(),
});
export type Schedule = z.infer<typeof ScheduleSchema>;

export const KidSummaryDeviceSchema = z.object({
  name: z.string(),
  platform: PlatformEnum,
  wg_ip: z.string(),
  online: z.boolean(),
  manual_block: z.boolean(),
});
export type KidSummaryDevice = z.infer<typeof KidSummaryDeviceSchema>;

export const KidSummarySchema = z.object({
  name: z.string(),
  age: z.number().nullable(),
  manual_block: z.boolean(),
  bonus_until: z.string().nullable(),
  schedule: ScheduleSchema,
  device_count: z.number(),
  online_device_count: z.number(),
  rule_count: z.number(),
  devices: z.array(KidSummaryDeviceSchema).default([]),
});
export type KidSummary = z.infer<typeof KidSummarySchema>;

export const StatsTopHostSchema = z.object({
  host: z.string(),
  requests: z.number(),
  pages: z.number(),
  blocked: z.number(),
});
export type StatsTopHost = z.infer<typeof StatsTopHostSchema>;

export const KidStatsSchema = z.object({
  kid: z.string(),
  last_seen: z.string().nullable(),
  requests_1h: z.number(),
  pages_1h: z.number(),
  blocked_1h: z.number(),
  requests_24h: z.number(),
  pages_24h: z.number(),
  blocked_24h: z.number(),
  top_hosts_1h: z.array(StatsTopHostSchema),
  sparkline_1h: z.array(z.number()),
  bucket_secs: z.number(),
});
export type KidStats = z.infer<typeof KidStatsSchema>;

export const KidStatsDetailSchema = KidStatsSchema.extend({
  top_hosts_24h: z.array(StatsTopHostSchema).default([]),
});
export type KidStatsDetail = z.infer<typeof KidStatsDetailSchema>;

export const KidDetailSchema = z.object({
  name: z.string(),
  age: z.number().nullable(),
  manual_block: z.boolean(),
  bonus_until: z.string().nullable(),
  schedule: ScheduleSchema,
  blocked_apps: z.array(z.string()),
  keyword_flags: z.array(z.string()),
  mitm_inspect_hosts: z.array(z.string()).default([]),
  mitm_passthrough_hosts: z.array(z.string()),
  mitm_passthrough_disabled: z.array(z.string()).default([]),
  devices: z.array(DeviceSchema),
  rules: z.array(RuleSchema),
});
export type KidDetail = z.infer<typeof KidDetailSchema>;

export const EventSchema = z.object({
  id: z.number().nullable(),
  // First seen (server `ts`) and last seen (server `ts_last`). The UI
  // sorts and renders against `ts_last`; `ts` is kept for forensic
  // context. `.nullish()` keeps an older bundle compatible with the
  // pre-Phase-1 server (and vice versa).
  ts: z.string().nullable(),
  ts_last: z.string().nullish(),
  hit_count: z.number().default(1),
  source: z.string(),
  client_ip: z.string(),
  kid: z.string().nullable(),
  device: z.string().nullable(),
  method: z.string().nullable(),
  host: z.string(),
  registrable: z.string().nullish(),
  path: z.string().nullable(),
  query: z.string().nullable(),
  status: z.number().nullable(),
  decision: z.string(),
  rule: z.string().nullable(),
  sni_only: z.boolean(),
  kind: z.string().nullable(),
});
export type Event = z.infer<typeof EventSchema>;

export const IosBrowserEnum = z.enum([
  "chrome",
  "safari",
  "firefox",
  "edge",
  "brave",
  "none",
]);
export type IosBrowser = z.infer<typeof IosBrowserEnum>;

export const AndroidBrowserEnum = z.enum([
  "chrome",
  "firefox",
  "edge",
  "brave",
  "samsung_internet",
  "none",
]);
export type AndroidBrowser = z.infer<typeof AndroidBrowserEnum>;

export const BrowserPolicySchema = z.object({
  ios: z.object({
    allowed_browser: IosBrowserEnum,
    extra_blocked: z.array(z.string()),
    unblocked: z.array(z.string()),
  }),
  android: z.object({
    allowed_browser: AndroidBrowserEnum,
    extra_blocked: z.array(z.string()),
    unblocked: z.array(z.string()),
  }),
  chrome_managed_config: z.object({
    incognito_disabled: z.boolean(),
    sync_disabled: z.boolean(),
    signin_disabled: z.boolean(),
    search_suggest_enabled: z.boolean(),
  }),
  // Server-computed effective view. Read-only — not sent on PUT.
  effective: z.object({
    ios_blocklist: z.array(z.string()),
    android_blocklist: z.array(z.string()),
    ios_chromium_appconfig_target: z.string().nullable(),
    android_force_install: z.string().nullable(),
  }).optional(),
});
export type BrowserPolicy = z.infer<typeof BrowserPolicySchema>;

export const BrowserCatalogEntrySchema = z.object({
  key: z.string(),
  label: z.string(),
  ios_supported: z.boolean(),
  android_supported: z.boolean(),
});
export type BrowserCatalogEntry = z.infer<typeof BrowserCatalogEntrySchema>;

export const SettingsSchema = z.object({
  ca_present: z.boolean(),
  ca_url: z.string(),
  ca_qr_url: z.string(),
  retention_days: z.number(),
  max_events: z.number(),
  tz: z.string(),
  wg_host: z.string(),
  wg_port: z.number(),
  adguard_ui_port: z.number(),
  adguard_admin_user: z.string(),
  adguard_admin_password: z.string(),
  internal_url: z.string(),
  db_stats: z.object({
    events: z.number(),
    oldest: z.string().nullable(),
    newest: z.string().nullable(),
    db_path: z.string(),
    db_bytes: z.number(),
  }),
  browser_policy: BrowserPolicySchema,
  available_browsers: z.array(BrowserCatalogEntrySchema),
});
export type Settings = z.infer<typeof SettingsSchema>;

export const EnrolmentSchema = z.object({
  device: DeviceSchema,
  qr_url: z.string(),
  conf_url: z.string(),
  ca_url: z.string(),
  ca_qr_url: z.string(),
  ca_present: z.boolean(),
});
export type Enrolment = z.infer<typeof EnrolmentSchema>;

export const TlsFailureChildSchema = z.object({
  id: z.number().nullable(),
  host: z.string(),
  device: z.string().nullable(),
  client_ip: z.string(),
  count: z.number(),
  error: z.string().nullish(),
  ts_first: z.string().nullable(),
  ts_last: z.string().nullable(),
});
export type TlsFailureChild = z.infer<typeof TlsFailureChildSchema>;

export const BulkCdnGroupSchema = z.object({
  vendor: z.string(),
  patterns: z.array(z.string()),
});
export type BulkCdnGroup = z.infer<typeof BulkCdnGroupSchema>;

export const TlsFailureGroupSchema = z.object({
  registrable: z.string(),
  kid: z.string().nullable(),
  // Legacy field — server stopped emitting `enabled` when auto-passthrough
  // was removed. Default true keeps old SPA bundles working against the new
  // backend (and vice versa).
  enabled: z.boolean().default(true),
  count: z.number(),
  ts_last: z.string().nullable(),
  error: z.string().nullish(),
  children: z.array(TlsFailureChildSchema),
});
export type TlsFailureGroup = z.infer<typeof TlsFailureGroupSchema>;

export const ShortlinkSchema = z.object({
  code: z.string(),
  url: z.string(),
});
export type Shortlink = z.infer<typeof ShortlinkSchema>;

export const DlResolveSchema = z.object({
  kid: z.string(),
  ip: z.string(),
  device_name: z.string(),
});
export type DlResolve = z.infer<typeof DlResolveSchema>;

export const HandshakeSchema = z.object({
  last_handshake: z.number(),
  rx: z.number(),
  tx: z.number(),
});
export type Handshake = z.infer<typeof HandshakeSchema>;

export const ServiceSchema = z.object({
  id: z.string(),
  name: z.string(),
  icon_svg: z.string().optional().default(""),
  group_id: z.string().optional().default(""),
  rules: z.array(z.string()).optional().default([]),
});
export type Service = z.infer<typeof ServiceSchema>;

export const ServiceGroupSchema = z.object({
  id: z.string(),
  // AdGuard's catalog ships group ids only (e.g. "ai", "cdn"); no display
  // name is provided over the API — the dashboard humanizes the id itself.
  name: z.string().optional(),
});
export type ServiceGroup = z.infer<typeof ServiceGroupSchema>;

export const ServiceCatalogSchema = z.object({
  groups: z.array(ServiceGroupSchema),
  services: z.array(ServiceSchema),
});
export type ServiceCatalog = z.infer<typeof ServiceCatalogSchema>;

export const ResourceContainerSchema = z.object({
  name: z.string(),
  state: z.string(),
  cpu_percent: z.number().nullable(),
  mem_used_bytes: z.number(),
  mem_limit_bytes: z.number(),
});
export type ResourceContainer = z.infer<typeof ResourceContainerSchema>;

export const ResourceListSchema = z.object({
  containers: z.array(ResourceContainerSchema),
});
export type ResourceList = z.infer<typeof ResourceListSchema>;
