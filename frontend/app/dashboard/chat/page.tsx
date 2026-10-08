"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, openDocument } from "../../../lib/api";
import "./learning.css";

type Workspace = { id: string; name: string };
type Document = { id: string; name: string; status: string; error: string | null; chunk_count: number; size_bytes: number; embedding_model: string };
type Source = { number: number; document_id: string; document_name: string; page: number | null; content: string; chunk_id: string };
type Message = { role: string; content: string; sources?: Source[]; ai_mode?: string };
type Session = { id: string; title: string };
type Config = { embedding_backend: string; embedding_model: string; llm_backend: string; max_upload_bytes: number };
type Answer = { session_id: string; answer: string; sources: Source[]; ai_mode: string };

export default function ChatPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [documents, setDocuments] = useState<Document[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [config, setConfig] = useState<Config | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);
  const pending = useRef<{ id: string; question: string; session: string; workspace: string } | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [spaces, configuration] = await Promise.all([api<Workspace[]>("/workspaces"), api<Config>("/learning/config")]);
        if (cancelled) return;
        setWorkspaces(spaces);
        setConfig(configuration);
        const saved = localStorage.getItem("asteria.workspace");
        const selected = spaces.find((space) => space.id === saved)?.id ?? spaces[0]?.id ?? "";
        setWorkspaceId(selected);
      } catch (e) { if (!cancelled) setError(e instanceof Error ? e.message : "Could not load workspaces"); }
      finally { if (!cancelled) setLoading(false); }
    }
    void load();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!workspaceId) return;
      setBusy("Loading materials...");
      try {
        const [docs, chats] = await Promise.all([api<Document[]>(`/workspaces/${workspaceId}/documents`), api<Session[]>(`/chat/sessions?workspace_id=${workspaceId}`)]);
        const saved = localStorage.getItem(`asteria.session.${workspaceId}`);
        const selected = chats.find((chat) => chat.id === saved)?.id ?? chats[0]?.id ?? "";
        const history = selected ? await api<Message[]>(`/chat/sessions/${selected}/messages`) : [];
        if (cancelled) return;
        setDocuments(docs); setSessions(chats); setSessionId(selected); setMessages(history);
        localStorage.setItem("asteria.workspace", workspaceId);
      } catch (e) { if (!cancelled) setError(e instanceof Error ? e.message : "Could not load materials"); }
      finally { if (!cancelled) setBusy(""); }
    }
    void load();
    return () => { cancelled = true; };
  }, [workspaceId]);

  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [messages, busy]);

  async function createWorkspace(e: React.FormEvent) {
    e.preventDefault(); setError(""); setBusy("Creating workspace...");
    try {
      const workspace = await api<Workspace>("/workspaces", { method: "POST", body: JSON.stringify({ name: workspaceName }) });
      setWorkspaces((previous) => [workspace, ...previous]); setWorkspaceId(workspace.id); setWorkspaceName("");
      setDocuments([]); setSessions([]); setSessionId(""); setMessages([]); pending.current = null;
    } catch (e) { setError(e instanceof Error ? e.message : "Create failed"); }
    finally { setBusy(""); }
  }

  async function refreshDocuments() { setDocuments(await api<Document[]>(`/workspaces/${workspaceId}/documents`)); }

  async function upload(file: File | undefined) {
    if (!file || !workspaceId) return;
    setError("");
    if (file.size > (config?.max_upload_bytes ?? 10 * 1024 * 1024)) { setError("Maximum file size is 10 MiB."); return; }
    setBusy("Parsing and indexing your document...");
    try {
      const data = new FormData(); data.append("file", file);
      const doc = await api<Document>(`/workspaces/${workspaceId}/documents`, { method: "POST", body: data });
      await refreshDocuments();
      if (doc.status === "failed") setError(doc.error ?? "Index failed. Try again.");
    } catch (e) { setError(e instanceof Error ? e.message : "Upload failed"); await refreshDocuments().catch(() => {}); }
    finally { setBusy(""); }
  }

  async function documentAction(doc: Document, action: "delete" | "reindex") {
    if (action === "delete" && !window.confirm(`Delete ${doc.name} and its index? Historical citation excerpts will remain in your chats.`)) return;
    setError(""); setBusy(action === "delete" ? "Deleting document..." : "Rebuilding index...");
    try {
      await api(`/workspaces/${workspaceId}/documents/${doc.id}${action === "reindex" ? "/reindex" : ""}`, { method: action === "delete" ? "DELETE" : "POST" });
      await refreshDocuments();
    } catch (e) { setError(e instanceof Error ? e.message : "Document action failed"); }
    finally { setBusy(""); }
  }

  async function selectSession(id: string) {
    setError(""); setBusy("Opening session...");
    try {
      const history = id ? await api<Message[]>(`/chat/sessions/${id}/messages`) : [];
      setSessionId(id); setMessages(history); pending.current = null;
      localStorage.setItem(`asteria.session.${workspaceId}`, id);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not open session"); }
    finally { setBusy(""); }
  }

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim() || !workspaceId || busy) return;
    const text = question.trim(); setError(""); setBusy("Finding evidence and preparing your answer...");
    if (!pending.current || pending.current.question !== text || pending.current.session !== sessionId || pending.current.workspace !== workspaceId) {
      pending.current = { id: crypto.randomUUID(), question: text, session: sessionId, workspace: workspaceId };
    }
    try {
      const answer = await api<Answer>("/chat", { method: "POST", body: JSON.stringify({ message: text, workspace_id: workspaceId, session_id: sessionId || null, request_id: pending.current.id }) });
      setMessages((previous) => [...previous, { role: "user", content: text }, { role: "assistant", content: answer.answer, sources: answer.sources, ai_mode: answer.ai_mode }]);
      setSessionId(answer.session_id); setQuestion(""); pending.current = null;
      localStorage.setItem(`asteria.session.${workspaceId}`, answer.session_id);
      setSessions(await api<Session[]>(`/chat/sessions?workspace_id=${workspaceId}`));
    } catch (e) { setError(e instanceof Error ? e.message : "Answer failed. Your question is kept for retry."); }
    finally { setBusy(""); }
  }

  if (loading) return <p role="status">Loading your learning spaces...</p>;
  const simulated = config?.llm_backend === "test" || config?.embedding_backend === "test";
  return (
    <div className="learning-page">
      <div className="learning-heading"><div><p className="eyebrow">PERSONAL LEARNING COPILOT</p><h1>Learn from your course materials</h1><p>Choose a course, add your notes, and ask questions with evidence.</p></div></div>
      {simulated && <div className="learning-notice" role="status">Test mode — {config?.embedding_backend === "test" ? "simulated embeddings" : "local BGE embeddings"}; {config?.llm_backend === "test" ? "simulated LLM answers" : "provider LLM"}. This is not a real AI end-to-end validation.</div>}
      {error && <div className="learning-error" role="alert">{error}</div>}
      <div className="learning-grid">
        <aside className="learning-sidebar">
          <section className="learning-card"><h2>Workspace</h2>
            <label htmlFor="workspace">Your course</label>
            <select id="workspace" value={workspaceId} disabled={Boolean(busy)} onChange={(e) => { setWorkspaceId(e.target.value); setMessages([]); setSessionId(""); setDocuments([]); setSessions([]); setError(""); pending.current = null; }}>
              <option value="" disabled>Select a workspace</option>{workspaces.map((space) => <option key={space.id} value={space.id}>{space.name}</option>)}
            </select>
            <form onSubmit={createWorkspace}><label htmlFor="workspace-name">New course name</label><input id="workspace-name" value={workspaceName} onChange={(e) => setWorkspaceName(e.target.value)} maxLength={120} placeholder="e.g. COMP101" required disabled={Boolean(busy)} /><button className="btn btn-secondary" disabled={Boolean(busy) || !workspaceName.trim()}>Create Workspace</button></form>
          </section>
          <section className="learning-card"><h2>Course materials</h2><p className="muted">Text PDF, Markdown or TXT · up to 10 MiB and 100 PDF pages.</p>
            <label className="upload-label" htmlFor="course-file">Upload course document</label><input id="course-file" type="file" accept=".pdf,.txt,.md" disabled={!workspaceId || Boolean(busy)} onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = ""; }} />
            {!documents.length && <p className="muted">Add a document to build your course index.</p>}
            {documents.map((doc) => <article className="document-item" key={doc.id}><strong>{doc.name}</strong><p><span className={`document-status ${doc.status}`}>{doc.status}</span> · {doc.chunk_count} chunks</p>{doc.error && <p className="document-error">{doc.error}</p>}<div className="document-actions"><button disabled={Boolean(busy)} onClick={() => void documentAction(doc, "reindex")}>Reindex</button><button disabled={Boolean(busy)} onClick={() => void documentAction(doc, "delete")}>Delete</button></div></article>)}
          </section>
          <section className="learning-card"><h2>Sessions</h2><button className="btn btn-secondary" disabled={!workspaceId || Boolean(busy)} onClick={() => void selectSession("")}>+ New Chat</button><p className="muted">Latest 100 sessions; latest 200 messages per session.</p>{sessions.map((session) => <button className={`session-button ${session.id === sessionId ? "active" : ""}`} key={session.id} disabled={Boolean(busy)} onClick={() => void selectSession(session.id)}>{session.title}</button>)}</section>
        </aside>
        <section className="learning-chat" aria-label="Knowledge chat">
          <div className="learning-chat-title"><h2>{workspaces.find((space) => space.id === workspaceId)?.name ?? "Your learning space"}</h2><span>Answers grounded in your documents</span></div>
          <div className="learning-messages" aria-live="polite">
            {!messages.length && <div className="learning-empty"><h3>What would you like to understand?</h3><p>Upload your course materials, then ask about a concept. Open each source to check the original passage.</p></div>}
            {messages.map((message, index) => <article className={`learning-message ${message.role}`} key={index}><strong className="message-author">{message.role === "user" ? "You" : "Asteria"}</strong><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
              {message.sources?.length ? <div className="sources"><h3>Sources</h3>{message.sources.map((source) => <details key={`${source.number}-${source.chunk_id}`}><summary>[{source.number}] {source.document_name} · {source.page ? `PDF page ${source.page}` : "Text excerpt"}{!documents.some((doc) => doc.id === source.document_id) ? " (deleted; saved excerpt)" : ""}</summary><blockquote>{source.content}</blockquote>{documents.some((doc) => doc.id === source.document_id) && <button onClick={() => void openDocument(workspaceId, source.document_id, source.document_name).catch((e) => setError(e.message))}>Download original</button>}</details>)}</div> : null}
            </article>)}
            {busy && <p role="status" className="muted">{busy}</p>}<div ref={end} />
          </div>
          <form className="learning-composer" onSubmit={send}><label htmlFor="question">Ask about your materials</label><div><textarea id="question" value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={4000} placeholder="What does the course say about...?" rows={2} disabled={!workspaceId || Boolean(busy)} /><button className="btn btn-primary" disabled={!workspaceId || Boolean(busy) || !question.trim()}>Send</button></div><p className="muted">When the materials are insufficient, Asteria will say so. Check source passages for accuracy.</p></form>
        </section>
      </div>
    </div>
  );
}
