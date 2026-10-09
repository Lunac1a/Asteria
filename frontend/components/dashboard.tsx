"use client";
import { useI18n, t, uiError, getLocale } from "../lib/i18n";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import Icon from "./icon";

type Workspace = { id: string; name: string; material_count: number; conversation_count: number };
type Conversation = { session_type?:string; learning_status?:string; focus?:string; id: string; title: string; workspace_id: string; workspace_name: string; updated_at: string };
type Data = { continue_learning?:Conversation; workspaces: Workspace[]; conversations: Conversation[] };
function destination(workspace: string, session?: string) {
  if (!session) return `/dashboard/workspaces/${encodeURIComponent(workspace)}`;
  const query = new URLSearchParams({ workspace });
  if (session) query.set("session", session);
  return "/dashboard/chat?" + query.toString();
}
function count(n: number, name: string) { return t(`{count} ${name}${n === 1 ? "" : "s"}`, {count: new Intl.NumberFormat(getLocale()).format(n)}); }
function dateLabel(value: string) {
  const date = new Date(value.endsWith("Z") || /[+-]\d\d:\d\d$/.test(value) ? value : value + "Z");
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString(getLocale(), { month: "short", day: "numeric" });
}
function WorkspaceCard({ space }: { space: Workspace }) {
 useI18n();
  return <Link className="workspace-card" href={destination(space.id)}><span className="workspace-icon"><Icon name="folder" size={30} /></span><div><h3>{space.name}</h3><p>{count(space.material_count, "material")} · {count(space.conversation_count, "conversation")}</p></div><Icon name="chevron" size={18} /></Link>;
}
export default function Dashboard({ listOnly = false }: { listOnly?: boolean }) {
 useI18n();
  const router = useRouter();
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [createError, setCreateError] = useState("");
  const [saving, setSaving] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    let cancelled = false;
    void api<Data>("/dashboard").then(value => { if (!cancelled) { setData(value); setError(""); } }).catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : "Could not load your dashboard."); });
    return () => { cancelled = true; };
  }, [retry]);
  useEffect(() => {
    if (creating) { dialog.current?.showModal(); dialog.current?.querySelector("input")?.focus(); }
    else dialog.current?.close();
  }, [creating]);
  function openCreate() { returnFocus.current = document.activeElement as HTMLElement; setCreateError(""); setCreating(true); }
  function closeCreate() { if (saving) return; setCreating(false); returnFocus.current?.focus(); }
  async function create(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || saving) return;
    setSaving(true); setCreateError("");
    try {
      const space = await api<{id: string}>("/workspaces", { method: "POST", body: JSON.stringify({name: name.trim()}) });
      router.push(destination(space.id));
    } catch (e) { setCreateError(e instanceof Error ? e.message : "Could not create workspace. Your name is still here."); setSaving(false); }
  }
  const hero = data?.continue_learning ?? data?.conversations[0];
  const activeLearning=hero?.session_type==="learning"&&hero.learning_status==="active";
  const recent = data?.conversations.filter(c=>c.id!==hero?.id) ?? [];
  return <div className="d1-dashboard">
    {error ? <section className="d1-feedback" role="alert"><h1>{t("Couldn’t load your learning space")}</h1><p>{uiError(error)}</p><button className="d1-button" onClick={() => { setError(""); setRetry(v => v + 1); }}>{t("Try again")}</button></section> : !data ? <section className="dashboard-loading" aria-busy="true" role="status"><p>{t("Loading your learning space…")}</p><div /><div /><div /></section> : data.workspaces.length === 0 ? <section className="first-use">
      <span className="empty-icon"><Icon name="book" size={30} /></span><h1>{t("Your learning space starts here.")}</h1><p>{t("Create a workspace to organize your materials,")}<br className="desktop-break" /> {t("ask questions and keep learning.")}</p><button className="d1-button" onClick={openCreate}><Icon name="plus" size={18} />{t("Create your first workspace")}</button><small>{t("Start with a subject, a course, or something you’re curious about.")}</small>
    </section> : <>
      <div className="dashboard-heading"><h1>{listOnly ? t("Your Workspaces") : t("Welcome back.")}</h1><p>{listOnly ? t("A space for everything you’re learning.") : t("Pick up where you left off.")}</p>{listOnly && <button className="d1-button" onClick={openCreate}><Icon name="plus" size={18} />{t("Create workspace")}</button>}</div>
      {!listOnly && <section className="continue-card" aria-labelledby="continue-heading"><div className="eyebrow"><Icon name={hero ? "chat" : "book"} />{hero ? (activeLearning ? t("Continue Learning") : hero.learning_status==="finished" ? t("Review conversation") : t("Continue conversation")) : t("Start a conversation")}</div><h2 id="continue-heading">{hero?.title || t("What would you like to explore?")}</h2><p>{hero?.workspace_name || t("Bring a question to your learning space.")}</p><div className="continue-bottom"><span>{hero ? (activeLearning&&hero.focus ? hero.focus : t("Your conversation is ready when you are.")) : t("Your materials and conversations stay together.")}</span><Link className="d1-button" href={hero ? destination(hero.workspace_id, hero.id) : destination(data.workspaces[0].id)}>{hero ? (activeLearning ? t("Continue Learning") : hero.learning_status==="finished" ? t("Review conversation") : t("Continue conversation")) : t("Open workspace")}<Icon name="arrow" /></Link></div></section>}
      <section className="dashboard-section" aria-label={t("Your workspaces")}>{!listOnly && <div className="section-heading"><h2>{t("Your Workspaces")}</h2><div><button className="d1-text-button" onClick={openCreate}><Icon name="plus" size={18} />{t("Create workspace")}</button><Link href="/dashboard/workspaces">{t("View all")}<Icon name="arrow" size={18} /></Link></div></div>}
        <div className="workspace-grid">{(listOnly ? data.workspaces : data.workspaces.slice(0, 3)).map(space => <WorkspaceCard key={space.id} space={space} />)}</div>
      </section>
      {!listOnly && <section className="dashboard-section"><div className="section-heading"><h2>{t("Recent Conversations")}</h2></div>{recent.length ? <div className="conversation-list">{recent.map(chat => <Link className="conversation-row" key={chat.id} href={destination(chat.workspace_id, chat.id)}><span className="conversation-icon"><Icon name="chat" size={26} /></span><div><h3>{chat.title}</h3><p>{chat.workspace_name}</p></div><time dateTime={chat.updated_at}>{dateLabel(chat.updated_at)}</time><Icon name="chevron" size={18} /></Link>)}</div> : <div className="recent-empty"><Icon name="chat" /><p>{hero ? t("Your latest conversation is just above. New conversations will appear here.") : t("Your conversations will appear here once you start exploring.")}</p></div>}</section>}
    </>}
    <dialog className="create-dialog" ref={dialog} onCancel={e => { e.preventDefault(); closeCreate(); }} onClick={e => { if (e.target === e.currentTarget) closeCreate(); }} aria-labelledby="create-title"><form onSubmit={create}><div className="dialog-heading"><h2 id="create-title">{t("Create a workspace")}</h2><button type="button" className="d1-icon-button" aria-label={t("Close dialog")} disabled={saving} onClick={closeCreate}><Icon name="close" /></button></div><p>{t("A place for your materials and conversations.")}</p><label htmlFor="workspace-name">{t("Workspace name")}</label><input id="workspace-name" className="input" autoFocus required maxLength={120} value={name} onChange={e => setName(e.target.value)} placeholder={t("What are you learning?")} disabled={saving} />{createError && <p className="d1-error" role="alert">{uiError(createError)}</p>}<div className="dialog-actions"><button className="d1-button secondary" type="button" onClick={closeCreate} disabled={saving}>{t("Cancel")}</button><button className="d1-button" disabled={saving || !name.trim()}>{saving ? t("Creating…") : t("Create workspace")}</button></div></form></dialog>
  </div>;
}
