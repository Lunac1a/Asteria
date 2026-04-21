"use client";

import { useEffect, useState } from "react";

type LLMSettingsResponse = {
  provider: string;
  model_name: string;
  base_url: string;
  has_api_key: boolean;
};

export default function SettingsPage() {
  const [apiKey, setApiKey] = useState("");
  const [modelName, setModelName] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://integrate.api.nvidia.com/v1");

  const [hasApiKey, setHasApiKey] = useState(false);
  const [provider, setProvider] = useState("nvidia");

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;

  useEffect(() => {
    const fetchSettings = async () => {
      setLoading(true);
      setError("");
      setSuccess("");

      try {
        const token = localStorage.getItem("token");

        if (!token) {
          setError("You are not logged in.");
          setLoading(false);
          return;
        }

        const res = await fetch(`${apiBaseUrl}/api/settings/llm`, {
          method: "GET",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
        });

        if (res.status === 404) {
          setLoading(false);
          return;
        }

        if (!res.ok) {
          const data = await res.json().catch(() => null);
          throw new Error(data?.detail || "Failed to load settings.");
        }

        const data: LLMSettingsResponse = await res.json();

        setProvider(data.provider || "nvidia");
        setModelName(data.model_name || "qwen/qwen3.5-122b-a10b");
        setBaseUrl(data.base_url || "https://integrate.api.nvidia.com/v1");
        setHasApiKey(Boolean(data.has_api_key));
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Failed to load settings.";
        setError(message);
      } finally {
        setLoading(false);
      }
    };

    fetchSettings();
  }, [apiBaseUrl]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError("");
    setSuccess("");

    try {
      const token = localStorage.getItem("token");

      if (!token) {
        throw new Error("You are not logged in.");
      }

      if (!apiKey.trim() && !hasApiKey) {
        throw new Error("Please enter your NVIDIA API key.");
      }

      const payload = {
        api_key: apiKey.trim(),
        model_name: modelName.trim(),
        base_url: baseUrl.trim(),
      };

      const res = await fetch(`${apiBaseUrl}/api/settings/llm`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(payload),
      });

      const data = await res.json().catch(() => null);

      if (!res.ok) {
        throw new Error(data?.detail || "Failed to save settings.");
      }

      setHasApiKey(true);
      setApiKey("");
      setSuccess("Settings saved successfully.");
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to save settings.";
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <main className="min-h-screen bg-[#0b1120] text-white px-6 py-10">
        <div className="max-w-2xl mx-auto">
          <div className="rounded-2xl border border-white/10 bg-white/5 p-6">
            Loading settings...
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[#0b1120] text-white px-6 py-10">
      <div className="max-w-2xl mx-auto">
        <div className="mb-8">
          <h1 className="text-3xl font-semibold tracking-tight">LLM Settings</h1>
          <p className="text-white/70 mt-2">
            Configure your NVIDIA NIM API key to enable chat.
          </p>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/5 p-6 shadow-xl">
          <div className="mb-6 flex items-center justify-between gap-4 rounded-xl border border-white/10 bg-white/5 px-4 py-3">
            <div>
              <p className="text-sm text-white/60">Provider</p>
              <p className="font-medium capitalize">{provider}</p>
            </div>

            <div className="text-right">
              <p className="text-sm text-white/60">API Key Status</p>
              <p className={hasApiKey ? "text-green-400 font-medium" : "text-yellow-400 font-medium"}>
                {hasApiKey ? "Configured" : "Not configured"}
              </p>
            </div>
          </div>

          {error && (
            <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-red-300">
              {error}
            </div>
          )}

          {success && (
            <div className="mb-4 rounded-xl border border-green-500/30 bg-green-500/10 px-4 py-3 text-green-300">
              {success}
            </div>
          )}

          <form onSubmit={handleSave} className="space-y-5">
            <div>
              <label className="mb-2 block text-sm font-medium text-white/80">
                NVIDIA API Key
              </label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={hasApiKey ? "API key already configured. Enter a new one to replace it." : "Enter your NVIDIA API key"}
                className="w-full rounded-xl border border-white/10 bg-[#111827] px-4 py-3 text-white outline-none transition focus:border-blue-400"
              />
              <p className="mt-2 text-xs text-white/50">
                The saved key is not shown again for security reasons.
              </p>
            </div>

            <div>
              <label className="mb-2 block text-sm font-medium text-white/80">
                Model Name
              </label>
              <input
                type="text"
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
                className="w-full rounded-xl border border-white/10 bg-[#111827] px-4 py-3 text-white outline-none transition focus:border-blue-400"
              />
            </div>

            <div>
              <label className="mb-2 block text-sm font-medium text-white/80">
                Base URL
              </label>
              <input
                type="text"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                className="w-full rounded-xl border border-white/10 bg-[#111827] px-4 py-3 text-white outline-none transition focus:border-blue-400"
              />
              <p className="mt-2 text-xs text-white/50">
                Use the API root only, not the full <code>/chat/completions</code> endpoint.
              </p>
            </div>

            <button
              type="submit"
              disabled={saving}
              className="inline-flex items-center justify-center rounded-xl bg-blue-500 px-5 py-3 font-medium text-white transition hover:bg-blue-400 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {saving ? "Saving..." : "Save Settings"}
            </button>
          </form>
        </div>
      </div>
    </main>
  );
}