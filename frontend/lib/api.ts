const API_BASE_URL = "";

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const trace = crypto.randomUUID();
  const started = performance.now();
  const token = localStorage.getItem("access_token");
  const measurement = { status: 0 };
  let outcome: "ok" | "http_error" | "network" | "aborted" = "ok";
  try {
    return await apiRequest<T>(path, init, trace, measurement);
  } catch (error) {
    outcome = measurement.status >= 400 ? "http_error" : error instanceof DOMException && error.name === "AbortError" ? "aborted" : "network";
    throw error;
  } finally {
    // Local, content-free timing. Does not delay the action, replay it, or send learning text.
    if (token) {
      void fetch("/api/runtime-metrics/client", {
        method: "POST", headers: { "Authorization": `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ trace_id: trace, elapsed_ms: Math.min(300000, performance.now() - started), status: measurement.status, outcome }),
        keepalive: true, signal: AbortSignal.timeout(5000),
      }).catch(() => undefined);
    }
  }
}

async function apiRequest<T>(path: string, init: RequestInit, trace: string, measurement: { status: number }): Promise<T> {
  const token = localStorage.getItem("access_token");
  const headers = new Headers(init.headers);
  headers.set("X-Asteria-Trace", trace);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_BASE_URL}/api${path}`, { ...init, headers, cache: "no-store" });
  measurement.status = response.status;
  if (response.status === 401) {
    localStorage.removeItem("access_token");
    // A hard navigation clears account-specific client state after token expiry.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/auth/login");
    throw new Error("Please log in again.");
  }
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(typeof detail === "string" ? detail : "Request failed. Please try again.");
  }
  return data as T;
}

export async function documentBlob(workspaceId: string, documentId: string) {
  const response = await fetch(`${API_BASE_URL}/api/workspaces/${workspaceId}/documents/${documentId}/file`, {
    headers: { Authorization: `Bearer ${localStorage.getItem("access_token")}` },
  });
  if (response.status === 401) {
    localStorage.removeItem("access_token");
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/auth/login");
    throw new Error("Please log in again.");
  }
  if (!response.ok) throw new Error("The original file is unavailable. It may have been removed. Upload a readable replacement if needed.");
  return response.blob();
}

export async function openDocument(workspaceId: string, documentId: string, name: string) {
  const url = URL.createObjectURL(await documentBlob(workspaceId, documentId));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
