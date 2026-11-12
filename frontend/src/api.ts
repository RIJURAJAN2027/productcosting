// Thin API client: bearer token, JSON helpers, downloads and multipart uploads.
const BASE = (import.meta as unknown as { env: Record<string, string> }).env.VITE_API_URL ?? "";
const TOKEN_KEY = "pc_token";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

export const token = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

function messageOf(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d as { msg?: string }).msg ?? JSON.stringify(d)).join("; ");
  if (detail && typeof detail === "object") {
    const d = detail as { message?: string; problems?: { message: string }[] };
    if (d.problems) return `${d.message ?? "Problems"}: ${d.problems.map((p) => p.message).slice(0, 5).join("; ")}`;
    if (d.message) return d.message;
  }
  return "Request failed";
}

async function request<T>(method: string, path: string, body?: unknown, raw = false): Promise<T> {
  const headers: Record<string, string> = {};
  const t = token.get();
  if (t) headers.Authorization = `Bearer ${t}`;
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(BASE + path, { method, headers, body: payload });
  if (res.status === 401) {
    token.clear();
    onUnauthorized();
  }
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, messageOf(detail), detail);
  }
  if (raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
  upload: <T>(path: string, fields: Record<string, string | Blob | null | undefined>) => {
    const fd = new FormData();
    Object.entries(fields).forEach(([k, v]) => {
      if (v !== null && v !== undefined && v !== "") fd.append(k, v);
    });
    return request<T>("POST", path, fd);
  },
  async download(path: string, fallbackName = "download") {
    const res = await request<Response>("GET", path, undefined, true);
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") ?? "";
    const m = /filename="?([^"]+)"?/.exec(cd);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = m ? m[1] : fallbackName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  },
};

export function qs(params: Record<string, string | number | undefined | null | (string | number)[]>) {
  const u = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    if (Array.isArray(v)) v.forEach((x) => u.append(k, String(x)));
    else u.set(k, String(v));
  });
  const s = u.toString();
  return s ? `?${s}` : "";
}
