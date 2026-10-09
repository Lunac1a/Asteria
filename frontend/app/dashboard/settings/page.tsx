"use client";
import { useI18n, t, uiError, setLocale } from "../../../lib/i18n";


import { useEffect, useState } from "react";
import "./settings.css";

type LLMSettingsResponse = {
  provider: string;
  model_name: string;
  base_url: string;
  has_api_key: boolean;
};

export default function SettingsPage() {
 const locale = useI18n();
  const [apiKey, setApiKey] = useState("");
  const [modelName, setModelName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");

  const [hasApiKey, setHasApiKey] = useState(false);
  const [provider, setProvider] = useState("openai_compatible");

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const apiBaseUrl = "";

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
          throw new Error(typeof data?.detail === "string" ? data.detail : "Failed to load settings.");
        }

        const data: LLMSettingsResponse = await res.json();

        setProvider(data.provider || "openai_compatible");
        setModelName(data.model_name || "");
        setBaseUrl(data.base_url || "");
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
        throw new Error("Please enter your provider API key.");
      }

      const payload = {
        provider,
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
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
        const fields: Record<string, string> = { base_url: "Base URL", model_name: "Model Name", api_key: "API Key", provider: "Service" };
        const validation = Array.isArray(data?.detail)
          ? data.detail.map((issue: { loc?: string[]; msg?: string }) => {
              const field = issue.loc?.at(-1) ?? "";
              return `${fields[field] ?? "Settings"}: ${issue.msg ?? "Invalid value"}`;
            }).join("; ")
          : "";
        throw new Error(typeof data?.detail === "string" ? data.detail : validation || "Settings could not be saved. Please try again.");
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
        <div className="card settings-card">{t("Loading settings...")}</div>
      </div>
    );
  }

  return (
    <div className="settings-page">
      <div className="settings-header">
        <h1 className="section-title">{t("Settings")}</h1>
        <p className="card-text">
          {t("Connect an OpenAI-compatible API.")}</p>
      </div>

      <section className="card settings-card" aria-labelledby="language-heading">
        <h2 id="language-heading">{t("Interface language")}</h2>
        <label className="settings-label" htmlFor="ui-language">{t("Language")}</label>
        <select id="ui-language" className="input" value={locale} onChange={event => setLocale(event.target.value === 'zh-CN' ? 'zh-CN' : 'en')}>
          <option value="en">English</option><option value="zh-CN">简体中文</option>
        </select>
        <p className="settings-helper">{t("Only changes the interface. Your content and AI response language stay unchanged.")}</p>
      </section>
      <div className="card settings-card">
        <div className="settings-status">
          <div className="settings-status-key">
            <p className="settings-meta-label">{t("API Key Status")}</p>
            <p
              className={
                hasApiKey
                  ? "settings-status-text configured"
                  : "settings-status-text missing"
              }
            >
              {hasApiKey ? t("Configured") : t("Not configured")}
            </p>
          </div>
        </div>

        {error && <div className="settings-alert error">{uiError(error)}</div>}

        {success && <div className="settings-alert success">{t(success)}</div>}

        <form onSubmit={handleSave} className="settings-form">
          <div className="settings-field">
            <label htmlFor="provider-url" className="settings-label">{t("Base URL")}</label>
            <input
              id="provider-url"
              type="url"
              placeholder="https://api.example.com/v1"
              required
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              className="input"
            />
            <p className="settings-helper">
              {t("Use the API root only, not the full")}{" "}
              <code>/chat/completions</code> {t("endpoint.")}
            </p>
          </div>

          <div className="settings-field">
            <label htmlFor="provider-key" className="settings-label">{t("API Key")}</label>
            <input
              id="provider-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={
                hasApiKey ? t("API key already configured. Enter a new one to replace it.") : t("Enter your provider API key")
              }
              className="input"
            />
            <p className="settings-helper">
              {t("The saved key is not shown again. Enter a new key when changing the URL.")}</p>
          </div>

          <div className="settings-field">
            <label htmlFor="provider-model" className="settings-label">{t("Model Name")}</label>
            <input
              id="provider-model"
              type="text"
              value={modelName}
              onChange={(e) => setModelName(e.target.value)}
              className="input"
              required
            />
            <p className="settings-helper">{t("Enter an exact model name available to your API key.")}</p>
          </div>

          <button type="submit" disabled={saving} className="btn btn-primary">
            {saving ? t("Saving...") : t("Save Settings")}
          </button>
        </form>
      </div>
    </div>
  );
}
