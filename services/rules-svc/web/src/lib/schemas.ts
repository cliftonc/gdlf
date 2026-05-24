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
  blocked_apps: z.array(z.string()),
  keyword_flags: z.array(z.string()),
  mitm_passthrough_hosts: z.array(z.string()),
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

export const TlsFailureChildSchema = z.object({
  id: z.number().nullable(),
  host: z.string(),
  device: z.string().nullable(),
  client_ip: z.string(),
  count: z.number(),
  ts_first: z.string().nullable(),
  ts_last: z.string().nullable(),
});
export type TlsFailureChild = z.infer<typeof TlsFailureChildSchema>;

export const TlsFailureGroupSchema = z.object({
  registrable: z.string(),
  kid: z.string().nullable(),
  count: z.number(),
  ts_last: z.string().nullable(),
  children: z.array(TlsFailureChildSchema),
});
export type TlsFailureGroup = z.infer<typeof TlsFailureGroupSchema>;

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
