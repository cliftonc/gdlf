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

export function useAddPassthrough(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (host: string) =>
      api(`/api/kids/${encodeURIComponent(name)}/passthrough`, {
        method: "POST",
        body: { host },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.kid(name) }),
  });
}

export function useRemovePassthrough(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (host: string) =>
      api(
        `/api/kids/${encodeURIComponent(name)}/passthrough/${encodeURIComponent(host)}`,
        { method: "DELETE" }
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.kid(name) }),
  });
}

export function useSetBlockedApps(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (blocked_apps: string[]) =>
      api(`/api/kids/${encodeURIComponent(name)}/blocked-apps`, {
        method: "PUT",
        body: { blocked_apps },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.kid(name) }),
  });
}

export function useSetPassthrough(name: string) {
  // Atomic replace — used by the group-switch UI so toggling on/off applies
  // both the apex and `*.<reg>` patterns (or removes them) in one shot.
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (hosts: string[]) =>
      api(`/api/kids/${encodeURIComponent(name)}/passthrough`, {
        method: "PUT",
        body: { hosts },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.kid(name) }),
  });
}

export function useDismissTlsFailure() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (failureId: number) =>
      api(`/api/tls-failures/${failureId}`, { method: "DELETE" }),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["tls-failures"], exact: false }),
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

// --- MDM mutations ---------------------------------------------------------

export function useMdmEnrollToken(kidName: string) {
  // Returns a fresh one-time enrolment URL for the device. We deliberately
  // don't invalidate the kid query here — the dashboard renders the URL
  // from the mutation result directly (it includes the random token).
  return useMutation({
    mutationFn: (ip: string) =>
      api<{ token: string; enroll_url: string; expires_at: string }>(
        `/api/devices/${encodeURIComponent(ip)}/mdm/enroll-token`,
        { method: "POST" }
      ),
    onSuccess: (_data, _ip) => {
      // Force a kid refetch so the new pending mdm state shows up
      void kidName;
    },
  });
}

export function useMdmInstallPolicy(kidName: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ip: string) =>
      api(`/api/devices/${encodeURIComponent(ip)}/mdm/install-policy`, { method: "POST" }),
    onSuccess: (_data, ip) => {
      qc.invalidateQueries({ queryKey: qk.kid(kidName) });
      qc.invalidateQueries({ queryKey: qk.mdmCommands(ip) });
    },
  });
}

export function useMdmPush(kidName: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ip: string) =>
      api(`/api/devices/${encodeURIComponent(ip)}/mdm/push`, { method: "POST" }),
    onSuccess: (_data, ip) => {
      qc.invalidateQueries({ queryKey: qk.mdmCommands(ip) });
      qc.invalidateQueries({ queryKey: qk.kid(kidName) });
    },
  });
}

export function useMdmEnqueueCommand(kidName: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ ip, request_type }: { ip: string; request_type: string }) =>
      api<{ command_uuid: string; push_error: string | null }>(
        `/api/devices/${encodeURIComponent(ip)}/mdm/command`,
        { method: "POST", body: { request_type } }
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: qk.mdmCommands(vars.ip) });
      qc.invalidateQueries({ queryKey: qk.kid(kidName) });
    },
  });
}

export function usePruneNow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api("/api/settings/prune", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.settings }),
  });
}
