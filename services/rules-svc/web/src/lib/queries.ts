import { useQuery } from "@tanstack/react-query";
import { api, withDl } from "./api";
import {
  BulkCdnGroupSchema,
  DlResolveSchema,
  EnrolmentSchema,
  EventSchema,
  HandshakeSchema,
  KidDetailSchema,
  KidStatsDetailSchema,
  KidStatsSchema,
  KidSummarySchema,
  MdmCommandsSchema,
  ServiceCatalogSchema,
  SettingsSchema,
  ShortlinkSchema,
  TlsFailureGroupSchema,
  type BulkCdnGroup,
  type DlResolve,
  type Enrolment,
  type Event,
  type Handshake,
  type KidDetail,
  type KidStats,
  type KidStatsDetail,
  type KidSummary,
  type MdmCommands,
  type ServiceCatalog,
  type Settings,
  type Shortlink,
  type TlsFailureGroup,
} from "./schemas";
import { z } from "zod";

export const qk = {
  me: ["me"] as const,
  kids: ["kids"] as const,
  statsOverview: ["stats", "overview"] as const,
  statsKid: (name: string) => ["stats", "kid", name] as const,
  kid: (name: string) => ["kid", name] as const,
  enrolment: (name: string, ip: string) => ["enrolment", name, ip] as const,
  handshake: (ip: string) => ["handshake", ip] as const,
  shortlink: (ip: string) => ["shortlink", ip] as const,
  resolveDl: (code: string) => ["dl-resolve", code] as const,
  activity: (params: ActivityParams) => ["activity", params] as const,
  tlsFailures: (kid: string | null | undefined) =>
    ["tls-failures", kid ?? null] as const,
  bulkCdns: ["bulk-cdns"] as const,
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

export function useStatsOverview() {
  return useQuery({
    queryKey: qk.statsOverview,
    queryFn: async () => {
      const data = await api<{ kids: unknown[] }>("/api/stats/overview");
      return z.array(KidStatsSchema).parse(data.kids) as KidStats[];
    },
    refetchInterval: 10_000,
    staleTime: 5_000,
  });
}

export function useKidStats(name: string) {
  return useQuery({
    queryKey: qk.statsKid(name),
    queryFn: async () => {
      const data = await api<unknown>(
        `/api/stats/kid/${encodeURIComponent(name)}`,
      );
      return KidStatsDetailSchema.parse(data) as KidStatsDetail;
    },
    refetchInterval: 10_000,
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

export function useEnrolment(name: string, ip: string, dlCode?: string | null) {
  return useQuery({
    queryKey: qk.enrolment(name, ip),
    queryFn: async () => {
      const data = await api<unknown>(
        withDl(
          `/api/kids/${encodeURIComponent(name)}/devices/${encodeURIComponent(ip)}/enrolment`,
          dlCode
        )
      );
      return EnrolmentSchema.parse(data) as Enrolment;
    },
  });
}

export function useHandshake(ip: string, enabled = true, dlCode?: string | null) {
  return useQuery({
    queryKey: qk.handshake(ip),
    queryFn: async () => {
      const data = await api<unknown>(
        withDl(`/api/devices/${encodeURIComponent(ip)}/handshake`, dlCode)
      );
      return HandshakeSchema.parse(data) as Handshake;
    },
    refetchInterval: enabled ? 2_000 : false,
    enabled,
  });
}

export function useShortlink(ip: string) {
  return useQuery({
    queryKey: qk.shortlink(ip),
    queryFn: async () => {
      try {
        const data = await api<unknown>(`/api/devices/${encodeURIComponent(ip)}/shortlink`);
        return ShortlinkSchema.parse(data) as Shortlink;
      } catch (e) {
        // 404 = no shortlink yet; treat as a normal empty state.
        if (e instanceof Error && /404/.test(e.message)) return null;
        if ((e as { status?: number })?.status === 404) return null;
        throw e;
      }
    },
    staleTime: 30_000,
  });
}

export function useResolveDl(code: string) {
  return useQuery({
    queryKey: qk.resolveDl(code),
    queryFn: async () => {
      const data = await api<unknown>(`/api/dl/${encodeURIComponent(code)}/resolve`);
      return DlResolveSchema.parse(data) as DlResolve;
    },
    staleTime: 60_000,
    retry: false,
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

export function useBulkCdns() {
  // Read-only — list comes from a Python constant baked into rules-svc, so
  // it doesn't change without a redeploy. Cache aggressively.
  return useQuery({
    queryKey: qk.bulkCdns,
    queryFn: async () => {
      const data = await api<{ groups: unknown[]; total: number }>(
        "/api/bulk-cdns",
      );
      return {
        groups: z.array(BulkCdnGroupSchema).parse(data.groups) as BulkCdnGroup[],
        total: data.total,
      };
    },
    staleTime: 5 * 60_000,
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
