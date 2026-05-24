import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import {
  EnrolmentSchema,
  EventSchema,
  HandshakeSchema,
  KidDetailSchema,
  KidSummarySchema,
  LibrarySchema,
  SettingsSchema,
  type Enrolment,
  type Event,
  type Handshake,
  type KidDetail,
  type KidSummary,
  type Library,
  type Settings,
} from "./schemas";
import { z } from "zod";

export const qk = {
  me: ["me"] as const,
  kids: ["kids"] as const,
  kid: (name: string) => ["kid", name] as const,
  enrolment: (name: string, ip: string) => ["enrolment", name, ip] as const,
  handshake: (ip: string) => ["handshake", ip] as const,
  activity: (params: ActivityParams) => ["activity", params] as const,
  settings: ["settings"] as const,
  library: ["library"] as const,
  ruleSuggest: (host: string, path: string) => ["rule-suggest", host, path] as const,
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

export function useSettings() {
  return useQuery({
    queryKey: qk.settings,
    queryFn: async () => SettingsSchema.parse(await api<unknown>("/api/settings")) as Settings,
  });
}

export function useLibrary() {
  return useQuery({
    queryKey: qk.library,
    queryFn: async () => LibrarySchema.parse(await api<unknown>("/api/rules/library")) as Library,
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
