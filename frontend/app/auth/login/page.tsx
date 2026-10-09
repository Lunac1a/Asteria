"use client";
import { useI18n, t, uiError } from "../../../lib/i18n";


import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import "../auth.css";

const API_BASE_URL = "";

export default function LoginPage() {
 useI18n();
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("access_token");

    if (token) {
      router.replace("/dashboard");
    }
  }, [router]);

  async function handleLogin(e: { preventDefault: () => void }) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const response = await fetch(`${API_BASE_URL}/api/login`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email,
          password,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        let errorMessage = "Login failed";

        if (typeof data.detail === "string") {
          errorMessage = data.detail;
        } else if (Array.isArray(data.detail) && data.detail.length > 0) {
          errorMessage = data.detail
            .map((item: { msg: string }) => item.msg)
            .join(", ");
        }

        throw new Error(errorMessage);
      }

      localStorage.setItem("access_token", data.access_token);
      router.replace("/dashboard");
    } catch (err) {
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("Something went wrong");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="auth-back">
          <Link href="/" className="back-link">
            {t("← Back")}</Link>
        </div>
        <h1 className="auth-title">{t("Welcome back")}</h1>
        <p className="auth-subtitle">
          {t("Sign in to continue learning with Asteria.")}</p>

        <form onSubmit={handleLogin} className="form-stack">
          <input
            type="email"
            placeholder={t("Email")}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="input"
          />

          <input
            type="password"
            placeholder={t("Password")}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            className="input"
          />

          <button type="submit" disabled={loading} className="btn btn-primary">
            {loading ? t("Logging in...") : t("Login")}
          </button>
        </form>

        {error && <p className="error-text">{uiError(error)}</p>}

        <p className="helper-text">
          {t("Don&apos;t have an account?")}{" "}
          <Link href="/auth/register" className="text-link">
            {t("Register")}</Link>
        </p>
      </div>
    </main>
  );
}
