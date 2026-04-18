const API_BASE_URL = "http://127.0.0.1:8080";

export async function getHealth() {
  const response = await fetch(`${API_BASE_URL}/api/health`, {
    method: "GET",
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error("Failed to fetch health status");
  }

  return response.json();
}