import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { qk } from "./queries";
import type { Platform, RuleAction } from "./schemas";

export function useLogin() {
  return useMutation({
    mutationFn: (password: string) => api("/api/auth/login", { method: "POST", body: { password } }),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api("/api/auth/logout", { method: "POST" }),
    onSuccess: () => qc.clear(),
  });
}

export function useCreateKid() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
      age: number | null;
      schedule_weekday: string;
      schedule_weekend: string;
    }) => api<{ kid: { name: string } }>("/api/kids", { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.kids }),
  });
}

export function useDeleteKid() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api(`/api/kids/${encodeURIComponent(name)}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.kids }),
  });
}

export function useUpdateSchedule(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { weekday: string; weekend: string }) =>
      api(`/api/kids/${encodeURIComponent(name)}/schedule`, { method: "PUT", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.kid(name) }),
  });
}

export function useGrantBonus(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (minutes: number) =>
      api(`/api/kids/${encodeURIComponent(name)}/bonus`, { method: "POST", body: { minutes } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.kid(name) });
      qc.invalidateQueries({ queryKey: qk.kids });
    },
  });
}

export function useClearBonus(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api(`/api/kids/${encodeURIComponent(name)}/bonus`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.kid(name) });
      qc.invalidateQueries({ queryKey: qk.kids });
    },
  });
}

export function useKidBlock(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (blocked: boolean) =>
      api(`/api/kids/${encodeURIComponent(name)}/block`, { method: "PUT", body: { blocked } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.kid(name) });
      qc.invalidateQueries({ queryKey: qk.kids });
    },
  });
}

export function useCreateDevice(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { device_name: string; platform: Platform }) =>
      api<{ wg_ip: string }>(`/api/kids/${encodeURIComponent(name)}/devices`, {
        method: "POST",
        body,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.kid(name) });
      qc.invalidateQueries({ queryKey: qk.kids });
    },
  });
}

export function useDeleteDevice(kidName: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ip: string) =>
      api(`/api/devices/${encodeURIComponent(ip)}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.kid(kidName) });
      qc.invalidateQueries({ queryKey: qk.kids });
    },
  });
}

export function useDeviceBlock(kidName: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ ip, blocked }: { ip: string; blocked: boolean }) =>
      api(`/api/devices/${encodeURIComponent(ip)}/block`, { method: "PUT", body: { blocked } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.kid(kidName) });
      qc.invalidateQueries({ queryKey: qk.kids });
    },
  });
}

export function useRegenerateDevice(kidName: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ip: string) =>
      api(`/api/devices/${encodeURIComponent(ip)}/regenerate`, { method: "POST" }),
    onSuccess: (_data: unknown, ip: string) => {
      qc.invalidateQueries({ queryKey: qk.kid(kidName) });
      qc.invalidateQueries({ queryKey: qk.enrolment(kidName, ip) });
      qc.invalidateQueries({ queryKey: qk.handshake(ip) });
    },
  });
}

export function useMarkMitmInstalled(kidName: string, ip: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (installed: boolean) =>
      api(`/api/devices/${encodeURIComponent(ip)}/mitm-installed`, {
        method: "PUT",
        body: { installed },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.enrolment(kidName, ip) });
      qc.invalidateQueries({ queryKey: qk.kid(kidName) });
    },
  });
}

export function useUpdateRule(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      idx,
      body,
    }: {
      idx: number;
      body: {
        action: RuleAction;
        match: string;
        query: string | null;
        flag: boolean;
        note: string | null;
      };
    }) =>
      api(`/api/kids/${encodeURIComponent(name)}/rules/${idx}`, {
        method: "PUT",
        body,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.kid(name) }),
  });
}

export function useAddRule(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      action: RuleAction;
      match: string;
      query: string | null;
      flag: boolean;
      note: string | null;
    }) =>
      api(`/api/kids/${encodeURIComponent(name)}/rules`, {
        method: "POST",
        body,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.kid(name) }),
  });
}

export function useDeleteRule(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (idx: number) =>
      api(`/api/kids/${encodeURIComponent(name)}/rules/${idx}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.kid(name) }),
  });
}

export function useMoveRule(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ idx, dir }: { idx: number; dir: "up" | "down" }) =>
      api(`/api/kids/${encodeURIComponent(name)}/rules/${idx}/move`, {
        method: "PATCH",
        body: { dir },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.kid(name) }),
  });
}

export function usePruneNow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api("/api/settings/prune", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.settings }),
  });
}
