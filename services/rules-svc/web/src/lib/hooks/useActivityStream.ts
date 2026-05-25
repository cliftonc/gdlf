import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { qk, type ActivityParams } from "../queries";

// Open an EventSource against /api/activity/stream. The server emits a
// single `{kind:"changed", kid}` ping per ingested event; we debounce
// pings and invalidate the activity + stats query caches so the SPA
// refetches everything from the source of truth (the `event` table).
//
// This is deliberately coarse: at the volume gdlf handles (3–10 kids), a
// 50-row refetch + a stats summary on each change is cheap, and the old
// push-into-cache architecture is what caused the SSE / paged / counter
// drift the user reported. Refetching from one query — `useActivity` —
// guarantees the list, counters, and per-kid tiles all match.
//
// On error: fall back to a 5s invalidation interval and retry the
// EventSource after 30s.
export function useActivityStream(params: ActivityParams) {
  const qc = useQueryClient();
  const key = qk.activity(params);

  useEffect(() => {
    let es: EventSource | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let debounceTimer: ReturnType<typeof setTimeout> | null = null;
    let alive = true;

    const invalidateAll = (kid: string | null) => {
      qc.invalidateQueries({ queryKey: key });
      qc.invalidateQueries({ queryKey: qk.statsOverview });
      if (kid) qc.invalidateQueries({ queryKey: qk.statsKid(kid) });
    };

    const schedule = (kid: string | null) => {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => invalidateAll(kid), 250);
    };

    const startPolling = () => {
      if (pollTimer) return;
      pollTimer = setInterval(() => invalidateAll(null), 5_000);
    };

    const stopPolling = () => {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const connect = () => {
      if (!alive) return;
      try {
        es = new EventSource("/api/activity/stream", { withCredentials: true });
      } catch {
        startPolling();
        return;
      }
      es.addEventListener("activity", (ev) => {
        try {
          const data = JSON.parse((ev as MessageEvent).data) as
            | { kind?: string; kid?: string | null }
            | undefined;
          if (!data || data.kind !== "changed") return;
          // The ping carries the kid so we can target one kid's stats
          // cache without invalidating every kid's tile.
          schedule(data.kid ?? null);
        } catch {
          /* malformed ping — ignore */
        }
      });
      es.onopen = () => stopPolling();
      es.onerror = () => {
        es?.close();
        es = null;
        startPolling();
        retryTimer = setTimeout(connect, 30_000);
      };
    };

    connect();
    return () => {
      alive = false;
      es?.close();
      stopPolling();
      if (debounceTimer) clearTimeout(debounceTimer);
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [qc, key, params.kid, params.decision, params.limit]);
}
