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
        const token = localStorage.getItem("access_token");

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
      const token = localStorage.getItem("access_token");

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
      <div className="settings-page">
        <div className="card settings-card">Loading settings...</div>
      </div>
    );
  }

  return (
    <div className="settings-page">
      <div className="settings-header">
        <h1 className="section-title">LLM Settings</h1>
        <p className="card-text">
          Configure your NVIDIA NIM API key to enable chat.
        </p>
      </div>

      <div className="card settings-card">
        <div className="settings-status">
          <div>
            <p className="settings-meta-label">Provider</p>
            <p className="settings-meta-value">{provider}</p>
          </div>

          <div className="settings-status-key">
            <p className="settings-meta-label">API Key Status</p>
            <p
              className={
                hasApiKey
                  ? "settings-status-text configured"
                  : "settings-status-text missing"
              }
            >
              {hasApiKey ? "Configured" : "Not configured"}
            </p>
          </div>
        </div>

        {error && <div className="settings-alert error">{error}</div>}

        {success && <div className="settings-alert success">{success}</div>}

        <form onSubmit={handleSave} className="settings-form">
          <div className="settings-field">
            <label className="settings-label">NVIDIA API Key</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={
                hasApiKey
                  ? "API key already configured. Enter a new one to replace it."
                  : "Enter your NVIDIA API key"
              }
              className="input"
            />
            <p className="settings-helper">
              The saved key is not shown again for security reasons.
            </p>
          </div>

          <div className="settings-field">
            <label className="settings-label">Model Name</label>
            <input
              type="text"
              value={modelName}
              onChange={(e) => setModelName(e.target.value)}
              className="input"
            />
          </div>

          <div className="settings-field">
            <label className="settings-label">Base URL</label>
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              className="input"
            />
            <p className="settings-helper">
              Use the API root only, not the full{" "}
              <code>/chat/completions</code> endpoint.
            </p>
          </div>

          <button type="submit" disabled={saving} className="btn btn-primary">
            {saving ? "Saving..." : "Save Settings"}
          </button>
        </form>
      </div>
    </div>
  );
}
