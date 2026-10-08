const API_BASE_URL = "";

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem("access_token");
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_BASE_URL}/api${path}`, { ...init, headers, cache: "no-store" });
  if (response.status === 401) {
    localStorage.removeItem("access_token");
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

export async function openDocument(workspaceId: string, documentId: string, name: string) {
  const response = await fetch(`${API_BASE_URL}/api/workspaces/${workspaceId}/documents/${documentId}/file`, {
    headers: { Authorization: `Bearer ${localStorage.getItem("access_token")}` },
  });
  if (!response.ok) throw new Error("Document unavailable. It may have been deleted.");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export async function getHealth() {
  const response = await fetch(`${API_BASE_URL}/api/health`, {
    method: "GET",
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error("Failed to fetch backend health status");
  }

  return response.json();
}
