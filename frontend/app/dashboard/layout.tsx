"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import "./dashboard.css";

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const [authenticated, setAuthenticated] = useState(false);
  const [authError, setAuthError] = useState("");

  useEffect(() => {
    let cancelled = false;
    if (!localStorage.getItem("access_token")) {
      router.replace("/auth/login");
      return;
    }
    void api("/me").then(() => {
      if (!cancelled) setAuthenticated(true);
    }).catch(() => {
      if (!cancelled) setAuthError("Could not verify your account. Check the local API and refresh.");
    });
    return () => { cancelled = true; };
  }, [router]);

  function handleLogout() {
    localStorage.removeItem("access_token");
    router.push("/auth/login");
  }

  if (!authenticated) return <p role="status">{authError || "Checking your account..."}</p>;

  return (
    <div className="app-shell">
      <header className="app-topbar">
        <div className="app-container">
          <div className="topbar-inner">
            <div className="topbar-left">
              <Link href="/dashboard" className="logo">
                <Image
                  className="brand-mark"
                  src="/asteria-logo.svg"
                  alt=""
                  width={48}
                  height={48}
                  priority
                />
                <span>Asteria</span>
              </Link>
            </div>

            <div className="topbar-center">
              <Link href="/dashboard/chat" className="topbar-link">
                Learn
              </Link>
            </div>

            <div className="topbar-right">
              <Link href="/dashboard/settings" className="topbar-link">
                Settings
              </Link>
              <button onClick={handleLogout} className="btn btn-secondary">
                Logout
              </button>
            </div>
          </div>
        </div>
      </header>

      <main className="app-main">
        <div className="app-container">{children}</div>
      </main>
    </div>
  );
}
