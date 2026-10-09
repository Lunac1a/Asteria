"use client";
import { useI18n, t } from "../lib/i18n";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import Icon from "./icon";

export default function AppShell({ children }: { children: React.ReactNode }) {
 useI18n();
  const pathname = usePathname();
  const router = useRouter();
  const [account, setAccount] = useState(false);
  const [mobile, setMobile] = useState(false);
  const accountRef = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const mobileRef = useRef<HTMLDialogElement>(null);
  const mobileTrigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!account) return;
    function dismiss(event: PointerEvent) {
      if (!accountRef.current?.contains(event.target as Node)) setAccount(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") { setAccount(false); trigger.current?.focus(); }
    }
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", dismiss); document.removeEventListener("keydown", escape); };
  }, [account]);
  useEffect(() => { if (mobile) mobileRef.current?.showModal(); else mobileRef.current?.close(); }, [mobile]);
  const home = pathname === "/dashboard";
  const workspaces = pathname.startsWith("/dashboard/workspaces") || pathname.startsWith("/dashboard/chat");
  function closeMobile() { setMobile(false); mobileTrigger.current?.focus(); }
  function logout() { localStorage.removeItem("access_token"); router.replace("/auth/login"); }
  function links() { return <><Link href="/dashboard" aria-current={home ? "page" : undefined} onClick={closeMobile}>{t("Home")}</Link><Link href="/dashboard/workspaces" aria-current={workspaces ? "page" : undefined} onClick={closeMobile}>{t("Workspaces")}</Link></>; }
  return <div className="asteria-shell">
    <a className="skip-link" href="#main-content">{t("Skip to content")}</a>
    <header className="asteria-topbar">
      <Link href="/dashboard" className="asteria-brand" aria-label={t("Asteria home")}><span className="asteria-mark" aria-hidden="true" />Asteria</Link>
      <nav className="asteria-nav" aria-label={t("Main navigation")}>{links()}</nav>
      <div className="asteria-actions">
        <div className="account-control" ref={accountRef}><button ref={trigger} className="account-trigger" aria-label={t("Account menu")} aria-expanded={account} aria-controls="account-menu" onClick={() => setAccount(!account)}><span><Icon name="user" /></span><Icon name="down" size={18} /></button>
          {account && <div id="account-menu" className="account-popover" onBlur={e => { if (!e.currentTarget.parentElement?.contains(e.relatedTarget as Node)) setAccount(false); }}><p>{t("Account")}</p><Link href="/dashboard/settings" onClick={() => setAccount(false)}>{t("Settings")}</Link><button onClick={logout}>{t("Sign out")}</button></div>}
        </div>
        <button ref={mobileTrigger} className="mobile-nav-trigger" onClick={() => setMobile(true)} aria-label={t("Open navigation")}><Icon name="menu" /></button>
      </div>
    </header>
    <dialog ref={mobileRef} className="mobile-navigation" onCancel={closeMobile} onClick={e => { if (e.target === e.currentTarget) closeMobile(); }} aria-label={t("Navigation")}><div><button className="d1-icon-button" onClick={closeMobile} aria-label={t("Close navigation")}><Icon name="close" /></button><nav aria-label={t("Mobile navigation")}>{links()}<Link href="/dashboard/settings" onClick={closeMobile}>{t("Settings")}</Link><button onClick={logout}>{t("Sign out")}</button></nav></div></dialog>
    <main id="main-content" tabIndex={-1} className={`asteria-content${pathname.startsWith("/dashboard/chat") ? " asteria-content-wide" : ""}`}>{children}</main>
  </div>;
}
