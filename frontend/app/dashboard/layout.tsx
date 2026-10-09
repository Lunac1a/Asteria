"use client";
import { useI18n, t, uiError } from "../../lib/i18n";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import AppShell from "../../components/app-shell";
import "./dashboard.css";
export default function AppLayout({ children }: { children: React.ReactNode }) {
 useI18n();
  const router = useRouter();
  const [authenticated, setAuthenticated] = useState(false);
  const [authError, setAuthError] = useState("");
  useEffect(() => {
    let cancelled = false;
    if (!localStorage.getItem("access_token")) { router.replace("/auth/login"); return; }
    void api("/me").then(() => { if (!cancelled) setAuthenticated(true); }).catch(() => {
      if (!cancelled) setAuthError("Could not verify your account. Please refresh and try again.");
    });
    return () => { cancelled = true; };
  }, [router]);
  if (!authenticated) return <div className="auth-check" role="status">{authError ? uiError(authError) : t("Checking your account…")}{authError && <button className="d1-button" onClick={() => window.location.reload()}>{t("Try again")}</button>}</div>;
  return <AppShell>{children}</AppShell>;
}
