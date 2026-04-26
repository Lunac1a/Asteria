"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import "./dashboard.css";

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();

  function handleLogout() {
    localStorage.removeItem("access_token");
    router.push("/auth/login");
  }

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
                Chat
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
