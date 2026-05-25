// Single fetch wrapper used by every TanStack Query / mutation hook.
// Same-origin cookies; on 401 the layout-level auth guard handles redirect.

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown, message: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

type FetchOpts = {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  headers?: Record<string, string>;
};

export async function api<T = unknown>(path: string, opts: FetchOpts = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const init: RequestInit = {
    method,
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(opts.headers ?? {}),
    },
    signal: opts.signal,
  };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);

  const res = await fetch(path, init);
  if (res.status === 204) return undefined as T;

  const isJson = (res.headers.get("content-type") ?? "").includes("application/json");
  const body = isJson ? await res.json().catch(() => null) : await res.text().catch(() => "");

  if (!res.ok) {
    const msg =
      (isJson && body && typeof body === "object" && "error" in body && typeof body.error === "string"
        ? body.error
        : null) ??
      (isJson && body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
        ? body.detail
        : null) ??
      `${method} ${path} failed with ${res.status}`;
    throw new ApiError(res.status, body, msg);
  }
  return body as T;
}

/** Append `?dl=<code>` to a path so an unauthenticated request gets accepted
 *  by the shortlink auth bypass on device-scoped endpoints. No-op when code
 *  is empty (regular cookie-authenticated path). */
export function withDl(path: string, dlCode?: string | null): string {
  if (!dlCode) return path;
  return path + (path.includes("?") ? "&" : "?") + "dl=" + encodeURIComponent(dlCode);
}
