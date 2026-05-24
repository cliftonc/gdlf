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
});
export type Device = z.infer<typeof DeviceSchema>;

export const RuleSchema = z.object({
  action: RuleActionEnum,
  match: z.string(),
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

export const KidSummarySchema = z.object({
  name: z.string(),
  age: z.number().nullable(),
  manual_block: z.boolean(),
  bonus_until: z.string().nullable(),
  schedule: ScheduleSchema,
  device_count: z.number(),
  online_device_count: z.number(),
  rule_count: z.number(),
});
export type KidSummary = z.infer<typeof KidSummarySchema>;

export const KidDetailSchema = z.object({
  name: z.string(),
  age: z.number().nullable(),
  manual_block: z.boolean(),
  bonus_until: z.string().nullable(),
  schedule: ScheduleSchema,
  blocklists: z.array(z.string()),
  blocked_apps: z.array(z.string()),
  keyword_flags: z.array(z.string()),
  devices: z.array(DeviceSchema),
  rules: z.array(RuleSchema),
});
export type KidDetail = z.infer<typeof KidDetailSchema>;

export const EventSchema = z.object({
  id: z.number().nullable(),
  ts: z.string().nullable(),
  source: z.string(),
  client_ip: z.string(),
  kid: z.string().nullable(),
  device: z.string().nullable(),
  method: z.string().nullable(),
  host: z.string(),
  path: z.string().nullable(),
  query: z.string().nullable(),
  status: z.number().nullable(),
  decision: z.string(),
  rule: z.string().nullable(),
  sni_only: z.boolean(),
  kind: z.string().nullable(),
});
export type Event = z.infer<typeof EventSchema>;

export const SettingsSchema = z.object({
  ca_present: z.boolean(),
  ca_url: z.string(),
  ca_qr_url: z.string(),
  retention_days: z.number(),
  max_events: z.number(),
  tz: z.string(),
  wg_host: z.string(),
  wg_port: z.number(),
  db_stats: z.object({
    events: z.number(),
    oldest: z.string().nullable(),
    newest: z.string().nullable(),
    db_path: z.string(),
    db_bytes: z.number(),
  }),
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

export const HandshakeSchema = z.object({
  last_handshake: z.number(),
  rx: z.number(),
  tx: z.number(),
});
export type Handshake = z.infer<typeof HandshakeSchema>;

export const LibrarySchema = z.object({
  blocklists: z.record(
    z.string(),
    z.object({ description: z.string(), sources: z.array(z.string()) })
  ),
  apps: z.record(
    z.string(),
    z.object({ hosts: z.array(z.string()), ip_ranges: z.array(z.string()) })
  ),
});
export type Library = z.infer<typeof LibrarySchema>;
