const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL;

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