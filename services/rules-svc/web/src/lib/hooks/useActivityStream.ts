import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { qk, type ActivityParams } from "../queries";
import { EventSchema, type Event } from "../schemas";

// Open an EventSource against /api/activity/stream, prepend incoming events
// into the matching activity Query cache. On error, fall back to a 5s
// invalidation interval and retry the EventSource after 30s.
export function useActivityStream(params: ActivityParams) {
  const qc = useQueryClient();
  const key = qk.activity(params);

  useEffect(() => {
    let es: EventSource | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let alive = true;

    const matches = (e: Event): boolean => {
      if (params.kid && e.kid !== params.kid) return false;
      if (params.decision && e.decision !== params.decision) return false;
      if (!params.sni && e.decision === "sni_only") return false;
      if (!params.assets && (e.kind ?? "page") !== "page") return false;
      return true;
    };

    const startPolling = () => {
      if (pollTimer) return;
      pollTimer = setInterval(() => qc.invalidateQueries({ queryKey: key }), 5_000);
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
          const parsed = EventSchema.parse(JSON.parse((ev as MessageEvent).data));
          if (!matches(parsed)) return;
          qc.setQueryData<Event[]>(key, (prev) => {
            const head = prev ?? [];
            return [parsed, ...head].slice(0, params.limit ?? 50);
          });
        } catch {
          /* malformed message — ignore */
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
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [qc, key, params.kid, params.decision, params.sni, params.assets, params.limit]);
}
