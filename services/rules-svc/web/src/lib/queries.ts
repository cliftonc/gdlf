import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import {
  EnrolmentSchema,
  EventSchema,
  HandshakeSchema,
  KidDetailSchema,
  KidSummarySchema,
  MdmCommandsSchema,
  ServiceCatalogSchema,
  SettingsSchema,
  TlsFailureGroupSchema,
  type Enrolment,
  type Event,
  type Handshake,
  type KidDetail,
  type KidSummary,
  type MdmCommands,
  type ServiceCatalog,
  type Settings,
  type TlsFailureGroup,
} from "./schemas";
import { z } from "zod";

export const qk = {
  me: ["me"] as const,
  kids: ["kids"] as const,
  kid: (name: string) => ["kid", name] as const,
  enrolment: (name: string, ip: string) => ["enrolment", name, ip] as const,
  handshake: (ip: string) => ["handshake", ip] as const,
  activity: (params: ActivityParams) => ["activity", params] as const,
  tlsFailures: (kid: string | null | undefined) =>
    ["tls-failures", kid ?? null] as const,
  settings: ["settings"] as const,
  services: ["services"] as const,
  ruleSuggest: (host: string, path: string) => ["rule-suggest", host, path] as const,
  mdmCommands: (ip: string) => ["mdm-commands", ip] as const,
};

export type ActivityParams = {
  kid?: string | null;
  decision?: string | null;
  sni?: boolean;
  assets?: boolean;
  limit?: number;
};

export function useKids() {
  return useQuery({
    queryKey: qk.kids,
    queryFn: async () => {
      const data = await api<{ kids: unknown[] }>("/api/kids");
      return z.array(KidSummarySchema).parse(data.kids) as KidSummary[];
    },
    refetchOnWindowFocus: true,
    staleTime: 5_000,
  });
}

export function useKid(name: string) {
  return useQuery({
    queryKey: qk.kid(name),
    queryFn: async () => {
      const data = await api<{ kid: unknown }>(`/api/kids/${encodeURIComponent(name)}`);
      return KidDetailSchema.parse(data.kid) as KidDetail;
    },
    staleTime: 5_000,
  });
}

export function useEnrolment(name: string, ip: string) {
  return useQuery({
    queryKey: qk.enrolment(name, ip),
    queryFn: async () => {
      const data = await api<unknown>(
        `/api/kids/${encodeURIComponent(name)}/devices/${encodeURIComponent(ip)}/enrolment`
      );
      return EnrolmentSchema.parse(data) as Enrolment;
    },
  });
}

export function useHandshake(ip: string, enabled = true) {
  return useQuery({
    queryKey: qk.handshake(ip),
    queryFn: async () => {
      const data = await api<unknown>(`/api/devices/${encodeURIComponent(ip)}/handshake`);
      return HandshakeSchema.parse(data) as Handshake;
    },
    refetchInterval: enabled ? 2_000 : false,
    enabled,
  });
}

export function useActivity(params: ActivityParams) {
  return useQuery({
    queryKey: qk.activity(params),
    queryFn: async () => {
      const search = new URLSearchParams();
      if (params.kid) search.set("kid", params.kid);
      if (params.decision) search.set("decision", params.decision);
      if (params.sni) search.set("sni", "true");
      if (params.assets) search.set("assets", "true");
      if (params.limit) search.set("limit", String(params.limit));
      const qs = search.toString();
      const data = await api<{ events: unknown[] }>(`/api/activity${qs ? "?" + qs : ""}`);
      return z.array(EventSchema).parse(data.events) as Event[];
    },
  });
}

export function useTlsFailures(kid: string | null | undefined) {
  return useQuery({
    queryKey: qk.tlsFailures(kid),
    queryFn: async () => {
      const qs = kid ? `?kid=${encodeURIComponent(kid)}` : "";
      const data = await api<{ groups: unknown[] }>(`/api/tls-failures${qs}`);
      return z.array(TlsFailureGroupSchema).parse(data.groups) as TlsFailureGroup[];
    },
    // Poll while the Passthrough tab is open — new failures arrive
    // asynchronously from the addon and the user expects to see them.
    refetchInterval: 8_000,
  });
}

export function useSettings() {
  return useQuery({
    queryKey: qk.settings,
    queryFn: async () => SettingsSchema.parse(await api<unknown>("/api/settings")) as Settings,
  });
}

export function useServices() {
  return useQuery({
    queryKey: qk.services,
    queryFn: async () =>
      ServiceCatalogSchema.parse(await api<unknown>("/api/services")) as ServiceCatalog,
    // Catalog is global and effectively static — cache aggressively.
    staleTime: 5 * 60_000,
  });
}

export function useMdmCommands(ip: string, enabled = true) {
  return useQuery({
    queryKey: qk.mdmCommands(ip),
    queryFn: async () => {
      const data = await api<unknown>(`/api/devices/${encodeURIComponent(ip)}/mdm/commands`);
      return MdmCommandsSchema.parse(data) as MdmCommands;
    },
    // Poll while the MDM panel is open — devices respond asynchronously
    // and the parent wants to see Acknowledged transitions live.
    refetchInterval: enabled ? 4_000 : false,
    enabled,
  });
}

export function useRuleSuggest(host: string, path: string, enabled = true) {
  return useQuery({
    queryKey: qk.ruleSuggest(host, path),
    queryFn: async () => {
      const search = new URLSearchParams();
      if (host) search.set("host", host);
      if (path) search.set("path", path);
      const data = await api<{ suggested: string }>(`/api/rules/suggest?${search.toString()}`);
      return data.suggested;
    },
    enabled: enabled && (!!host || !!path),
  });
}
