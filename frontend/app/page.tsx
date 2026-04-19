import { getHealth } from "@/lib/api";
import { Analytics } from "@vercel/analytics/next"

export default async function Home() {
  let backendStatus = "unknown";

  try {
    const data = await getHealth();
    backendStatus = data.status;
  } catch (error) {
    backendStatus = "backend not reachable";
  }

  return (
    <main className="p-8">
      <h1 className="text-3xl font-bold">Personal Knowledge Copilot</h1>
      <p className="mt-4">Frontend is running.</p>
      <p className="mt-2">Backend status: {backendStatus}</p>
    </main>
  );
}