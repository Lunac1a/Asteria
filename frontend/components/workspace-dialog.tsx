"use client";
import { useI18n, t } from "../lib/i18n";

import { useEffect, useRef } from "react";
import Icon from "./icon";
export default function WorkspaceDialog({ title, children, close, busy = false, wide = false }: { title: string; children: React.ReactNode; close: () => void; busy?: boolean; wide?: boolean }) {
 useI18n();
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  useEffect(() => {
    opener.current = document.activeElement as HTMLElement;
    const dialog = ref.current;
    dialog?.showModal();
    return () => { dialog?.close(); opener.current?.focus(); };
  }, []);
  return <dialog ref={ref} className={`ws-dialog${wide ? " ws-dialog-wide" : ""}`} aria-label={title} onCancel={e => { e.preventDefault(); if (!busy) close(); }} onClick={e => { if(e.target === e.currentTarget && !busy) close(); }}><div className="ws-dialog-inner"><header><h2>{title}</h2><button className="d1-icon-button" onClick={close} disabled={busy} aria-label={t("Close dialog")}><Icon name="close" /></button></header>{children}</div></dialog>;
}
