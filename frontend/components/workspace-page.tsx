"use client";
import { useI18n, t, uiError, uiNotice, getLocale } from "../lib/i18n";

import Link from "next/link";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, openDocument } from "../lib/api";
import { uploadMaterial } from "../lib/material-upload";
import { chatPath, dateText, dateValue, fileRecovery, sessionLabel, sortMaterials, workspacePath, type Material, type Conversation } from "../lib/workspace-model";
import Icon from "./icon";
import WorkspaceDialog from "./workspace-dialog";
import NewChatButton from "./new-chat";
import "./workspace.css";
const MaterialPreview = dynamic(()=>import("./material-preview"));
type Space = {id:string;name:string};
type Config = {max_upload_bytes:number;embedding_backend:string;embedding_model:string};
const pageSize=30;
function Empty({kind,action}:{kind:"materials"|"conversations";action:React.ReactNode}) {
 useI18n();
  return <div className="ws-empty"><span><Icon name={kind === "materials" ? "folder" : "chat"} size={28}/></span><h3>{kind === "materials" ? t("Your materials belong here.") : t("A conversation starts with curiosity.")}</h3><p>{kind === "materials" ? t("Add a reading, your notes, or a document to explore together.") : t("Ask a question or explore an idea. No materials required.")}</p>{action}</div>;
}
function ConversationRow({chat,workspaceId}:{chat:Conversation;workspaceId:string}) {
 useI18n();
  return <Link className="ws-conversation" href={chatPath(workspaceId,chat.id)}><span className={`ws-chat-icon ${chat.session_type === "learning" ? "violet" : ""}`}><Icon name={chat.session_type === "learning" ? "cap" : "chat"}/></span><div><h3>{chat.title}</h3><p>{t(sessionLabel(chat))}</p></div><time dateTime={chat.updated_at}>{dateText(chat.updated_at, getLocale())}</time><Icon name="chevron" size={18}/></Link>;
}
export default function WorkspacePage({workspaceId,view}:{workspaceId:string;view:"overview"|"materials"|"conversations"}) {
 useI18n();
  const [space,setSpace]=useState<Space|null>(null);
  const [documents,setDocuments]=useState<Material[]>([]);
  const [conversations,setConversations]=useState<Conversation[]>([]);
  const [config,setConfig]=useState<Config|null>(null);
  const [loading,setLoading]=useState(true);
  const [loadError,setLoadError]=useState("");
  const [refresh,setRefresh]=useState(0);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [sort,setSort]=useState("recent");
  const [chatSort,setChatSort]=useState("recent");
  const [more,setMore]=useState(false);
  const [chatBusy,setChatBusy]=useState(false);
  const [busy,setBusy]=useState("");
  const [upload,setUpload]=useState<{name:string;phase:string}|null>(null);
  const [preview,setPreview]=useState<Material|null>(null);
  const [deleting,setDeleting]=useState<Material|null>(null);
  const [replacement,setReplacement]=useState<Material|null>(null);
  const [checkedAt,setCheckedAt]=useState(0);
  const fileInput=useRef<HTMLInputElement>(null);
  const operation=useRef(false);
  const materialVersion=useRef(0);
  const chatVersion=useRef(0);
  const base=`/workspaces/${encodeURIComponent(workspaceId)}`;
  const root=workspacePath(workspaceId);
  const newChat=<NewChatButton workspaceId={workspaceId} workspaceName={space?.name ?? "Workspace"}/>;
  const refreshDocuments=useCallback(async()=> {
    const version=materialVersion.current;
    const result=await api<Material[]>(`${base}/documents`);
    if(version===materialVersion.current){setDocuments(result);setCheckedAt(Date.now());} return result;
  },[base]);
  useEffect(()=>{
    let cancelled=false;
    void Promise.all([api<Space>(base),api<Material[]>(`${base}/documents`),api<Conversation[]>(`/chat/sessions?workspace_id=${encodeURIComponent(workspaceId)}&limit=${pageSize}`),api<Config>("/learning/config")]).then(([current,docs,chats,settings])=>{
      if(cancelled)return;
      setSpace(current);setDocuments(docs);setCheckedAt(Date.now());setConversations(chats);setConfig(settings);setMore(chats.length===pageSize);setLoadError("");setChatSort("recent");
    }).catch(e=>{if(!cancelled)setLoadError(e instanceof Error ? e.message : "Could not load this workspace.");}).finally(()=>{if(!cancelled)setLoading(false);});
    return ()=>{cancelled=true;};
  },[base,workspaceId,refresh]);
  const processing=documents.some(doc=>doc.status==="processing");
  const uploading=Boolean(upload);
  useEffect(()=>{
    if(!processing && !uploading)return;
    let cancelled=false;
    // Serialized polling avoids stale overlapping responses while a file is prepared.
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      const version=materialVersion.current;
      try {const docs=await api<Material[]>(`${base}/documents`);if(!cancelled&&version===materialVersion.current){setDocuments(docs);setCheckedAt(Date.now());}}
      catch {if(!cancelled)setError("Could not refresh processing status. Your materials are still here. Use Refresh to check again.");}
      if(!cancelled)timer=setTimeout(()=>void poll(),3000);
    }
    timer=setTimeout(()=>void poll(),1500);
    return ()=>{cancelled=true;clearTimeout(timer);};
  },[base,processing,uploading]);
  function chooseFile() {fileInput.current?.click();}
  async function addFile(file:File|undefined) {
    if(!file || operation.current)return;
    setError("");setNotice("");
    if(!/\.(pdf|md|txt)$/i.test(file.name)){setError("Choose a text-based PDF, Markdown or TXT file.");return;}
    if(!file.size){setError("This file is empty. Choose a file with readable content.");return;}
    if(file.size>(config?.max_upload_bytes ?? 10485760)){setError("This file is too large. Choose a file smaller than 10 MiB.");return;}
    const original=replacement;
    setReplacement(null);operation.current=true;materialVersion.current++;setBusy("upload");setUpload({name:file.name,phase:"Uploading…"});
    try {
      const result=await uploadMaterial(workspaceId,file,()=>setUpload({name:file.name,phase:"Preparing…"}));
      setDocuments(prev=>[result,...prev.filter(doc=>doc.id!==result.id)]);
      setNotice(result.status === "ready" ? original ? `Corrected file ready. “${original.name}” has been kept so its history stays clear. You can remove it from its file menu when you are ready.` : "Your material is ready to use." : "Your file was uploaded, but could not be prepared. See its recovery options below.");
    }catch(e){setError(e instanceof Error?e.message:"Upload failed. Your other materials have been kept.");}
    finally{operation.current=false;materialVersion.current++;setBusy("");setUpload(null);void refreshDocuments().catch(()=>setError(current=>current || "Could not refresh materials. Use Refresh before uploading again."));}
  }
  async function processFile(doc:Material) {
    if(operation.current)return;
    operation.current=true;materialVersion.current++;setBusy(doc.id);setError("");setNotice("");
    try {
      const result=await api<Material>(`${base}/documents/${doc.id}/reindex`,{method:"POST"});
      setDocuments(prev=>prev.map(item=>item.id===result.id?result:item));
      setNotice(result.status==="ready"?"Your material is ready to use.":"Preparation did not finish. See the file’s recovery options.");
    }catch(e){setError(e instanceof Error?e.message:"Could not reprocess this file.");}
    finally{operation.current=false;materialVersion.current++;setBusy("");void refreshDocuments().catch(()=>{});}
  }
  async function remove() {
    if(!deleting || operation.current)return;
    const doc=deleting;operation.current=true;materialVersion.current++;setBusy(doc.id);setError("");
    try{await api(`${base}/documents/${doc.id}`,{method:"DELETE"});setDocuments(prev=>prev.filter(item=>item.id!==doc.id));setDeleting(null);setNotice("Material removed. Saved citation excerpts remain in your conversations.");}
    catch(e){setError(e instanceof Error?e.message:"Could not delete this file. It is still in your materials.");}
    finally{operation.current=false;materialVersion.current++;setBusy("");}
  }
  async function loadChats(order:string,append=false) {
    const version=++chatVersion.current;setChatBusy(true);setError("");
    try{const result=await api<Conversation[]>(`/chat/sessions?${new URLSearchParams({workspace_id:workspaceId,sort:order,limit:String(pageSize),offset:String(append?conversations.length:0)})}`);
      if(version!==chatVersion.current)return;
      setConversations(prev=>append?[...prev,...result.filter(item=>!prev.some(old=>old.id===item.id))]:result);setChatSort(order);setMore(result.length===pageSize);
    }catch(e){if(version===chatVersion.current)setError(e instanceof Error?e.message:"Could not load conversations. Please try again.");}
    finally{if(version===chatVersion.current)setChatBusy(false);}
  }
  function materialRow(doc:Material,summary=false) {
    const needsUpdate=doc.status==="ready" && config && doc.embedding_model!==`${config.embedding_backend}:${config.embedding_model}`;
    const working=doc.status==="processing" || busy===doc.id;
    const stale=doc.status==="processing" && checkedAt-dateValue(doc.created_at)>120000;
    const recovery=fileRecovery(doc.error);
    const label=working?"Preparing…":needsUpdate?"Update needed":doc.status==="ready"?"Ready to use":"Couldn't process";
    const status=working?"preparing":needsUpdate?"update":doc.status;
    return <div className={`ws-material-row ${summary?"ws-material-summary":""}`} key={doc.id}>
      <div className="ws-material-name"><span className={`ws-file-icon ${doc.name.toLowerCase().endsWith('.pdf')?'pdf':''}`}><Icon name="file" size={24}/></span><div><h3>{doc.name}</h3><p>{doc.name.split('.').pop()?.toUpperCase()} · {Math.max(1,Math.round(doc.size_bytes/1024))} KB</p></div></div>
      <div className="ws-material-status"><span className={`ws-status ${status}`}><span aria-hidden="true">{working ? "◌" : doc.status==="ready"&&!needsUpdate ? "✓" : "!"}</span>{t(label)}</span>{!summary && doc.status==="failed" && !working && <p>{t(recovery.message)}</p>}{!summary && needsUpdate && <p>{t("Prepare this material for your current workspace settings.")}</p>}{!summary && stale && <p>{t("Taking longer than expected. Refresh or try processing again.")}</p>}</div>
      {!summary && <time dateTime={doc.created_at}>{dateText(doc.created_at, getLocale())}</time>}
      <div className="ws-material-actions">{summary ? <>{doc.status==="ready"?<button className="d1-text-button" onClick={()=>setPreview(doc)}>{t("Preview")}<Icon name="arrow" size={18}/></button>:<Link className="d1-text-button" href={`${root}/materials`}>{t("View status")}<Icon name="arrow" size={18}/></Link>}</> : <>
        {doc.status==="ready" && !working && !needsUpdate && <button className="d1-text-button" onClick={()=>setPreview(doc)}>{t("Preview")}</button>}
        {((doc.status==="failed" && recovery.action==="replace") || needsUpdate || stale || (doc.status==="failed" && recovery.action!=="replace")) && !busy && <button className="d1-button secondary" onClick={()=>{if(doc.status==="failed"&&recovery.action==="replace")setReplacement(doc);else void processFile(doc);}}>{doc.status==="failed"&&recovery.action==="replace" ? t("Upload corrected file") : t("Reprocess")}</button>}
        <details className="ws-file-menu" name="material-actions"><summary aria-label={t("Actions for {name}",{name:doc.name})}><Icon name="more"/></summary><div><button onClick={e=>{e.currentTarget.closest('details')?.removeAttribute('open');setPreview(doc);}}>{t("Preview original")}</button><button onClick={e=>{e.currentTarget.closest('details')?.removeAttribute('open');void openDocument(workspaceId,doc.id,doc.name).catch(e=>setError(e.message));}}>{t("Download")}</button>{!(doc.status==="failed"&&recovery.action==="replace") && <button disabled={Boolean(busy)||working&&!stale} onClick={e=>{e.currentTarget.closest('details')?.removeAttribute('open');void processFile(doc);}}>{t("Reprocess")}</button>}<button disabled={Boolean(busy)} onClick={e=>{e.currentTarget.closest('details')?.removeAttribute('open');setReplacement(doc);}}>{t("Upload corrected file")}</button><button className="ws-delete" disabled={Boolean(busy)||working&&!stale} onClick={e=>{e.currentTarget.closest('details')?.removeAttribute('open');setError("");setDeleting(doc);}}>{t("Delete")}</button></div></details>
      </>}</div>
    </div>;
  }
  const addButton=<button className="d1-button secondary" disabled={Boolean(busy)} onClick={chooseFile}><Icon name="plus" size={18}/>{t("Add materials")}</button>;
  if(loading)return <div className="ws-page" role="status"><p>{t("Loading your workspace…")}</p><div className="ws-loading"/><div className="ws-loading"/></div>;
  if(loadError || !space)return <div className="ws-page ws-load-error"><h1>{t("Couldn’t open this workspace")}</h1><p role="alert">{loadError ? uiError(loadError) : t("This workspace is unavailable.")}</p><div><button className="d1-button" onClick={()=>{setLoading(true);setRefresh(v=>v+1);}}>{t("Try again")}</button><Link className="d1-text-button" href="/dashboard/workspaces">{t("Back to Workspaces")}</Link></div></div>;
  return <div className="ws-page">
    <nav className="ws-breadcrumb" aria-label={t("Breadcrumb")}><Link href="/dashboard/workspaces">{t("Workspaces")}</Link><span>/</span><span>{space.name}</span></nav>
    <header className="ws-heading"><div><h1>{space.name}</h1><p>{t("A space for questions, ideas, and deeper understanding.")}</p></div>{newChat}</header>
    <nav className="ws-tabs" aria-label={t("Workspace views")}>{([['overview','Overview'],['materials','Materials'],['conversations','Conversations']] as const).map(([key,label])=><Link key={key} href={key==='overview'?root:`${root}/${key}`} aria-current={view===key?'page':undefined}>{t(label)}</Link>)}</nav>
    <input ref={fileInput} className="ws-hidden-input" type="file" accept=".pdf,.md,.txt" aria-label={t("Upload material")} onChange={e=>{void addFile(e.target.files?.[0]);e.target.value="";}}/>
    {error && !deleting && <div role="alert" className="ws-error"><p>{uiError(error)}</p><button className="d1-text-button" onClick={()=>{setError("");if(view==='conversations')void loadChats(chatSort);else void refreshDocuments().catch(e=>setError(e.message));}}>{t("Refresh")}</button></div>}
    {notice && <div role="status" className="ws-notice">{uiNotice(notice)}<button className="d1-icon-button" aria-label={t("Dismiss notification")} onClick={()=>setNotice("")}><Icon name="close" size={18}/></button></div>}
    {upload && <div role="status" className="ws-upload-status"><span className="ws-spinner"/>{t(upload.phase)} <strong>{upload.name}</strong><span>{t("You can keep exploring while we prepare your file.")}</span></div>}
    {view!=='conversations' && <section className="ws-section"><div className="ws-section-heading"><h2>{t("Materials")}</h2>{view==='overview'?<Link className="d1-text-button" href={`${root}/materials`}>{t("View all materials")}<Icon name="arrow" size={18}/></Link>:addButton}</div>
      {view==='materials' && <div className="ws-toolbar"><span>{t("PDF, Markdown or TXT · Up to 10 MiB per file")}</span><div><label>{t("Sort by")}<select aria-label={t("Sort materials")} value={sort} onChange={e=>setSort(e.target.value)}><option value="recent">{t("Newest added")}</option><option value="oldest">{t("Oldest added")}</option><option value="name">{t("File name")}</option></select></label><button className="d1-text-button" onClick={()=>{setError("");void refreshDocuments().catch(e=>setError(e.message));}}>{t("Refresh")}</button></div></div>}
      {!documents.length ? (upload ? <p className="ws-footnote">{t("Your file will appear here as it is prepared.")}</p> : <Empty kind="materials" action={addButton}/>) : <div className="ws-materials">{view==='materials' && <div className="ws-table-heading"><span>{t("Name")}</span><span>{t("Status")}</span><span>{t("Added")}</span><span className="ws-sr-only">{t("Actions")}</span></div>}{sortMaterials(documents,view==='overview'?'recent':sort).slice(0,view==='overview'?2:undefined).map(doc=>materialRow(doc,view==='overview'))}{view==='overview' && <div className="ws-add-row">{addButton}</div>}</div>}
      {view==='materials' && <p className="ws-footnote">{t("You can start a conversation while your materials are being prepared. Same-name uploads are kept as separate files.")}</p>}
    </section>}
    {view!=='materials' && <section className="ws-section"><div className="ws-section-heading"><h2>{t("Conversations")}</h2>{view==='overview'?<Link className="d1-text-button" href={`${root}/conversations`}>{t("View all conversations")}<Icon name="arrow" size={18}/></Link>:<label className="ws-sort">{t("Sort by")}<select aria-label={t("Sort conversations")} value={chatSort} disabled={chatBusy} onChange={e=>void loadChats(e.target.value)}><option value="recent">{t("Last active")}</option><option value="oldest">{t("Oldest activity")}</option><option value="title">{t("Title A–Z")}</option></select></label>}</div>
      {chatBusy && <p role="status">{t("Loading conversations…")}</p>}{!conversations.length?<Empty kind="conversations" action={newChat}/>:<div className="ws-conversations">{conversations.slice(0,view==='overview'?3:undefined).map(chat=><ConversationRow key={chat.id} chat={chat} workspaceId={workspaceId}/>)}</div>}
      {view==='conversations' && more && <button className="d1-button secondary ws-load-more" disabled={chatBusy} onClick={()=>void loadChats(chatSort,true)}>{chatBusy ? t("Loading…") : t("Load more conversations")}</button>}
    </section>}
    {preview && <MaterialPreview material={preview} workspaceId={workspaceId} close={()=>setPreview(null)}/>}
    {replacement && <WorkspaceDialog title={t("Upload a corrected file")} close={()=>setReplacement(null)}><p>{t("Choose a text-based PDF, UTF-8 Markdown or TXT file for")}<strong>{replacement.name}</strong>.</p><p>{t("The new file is added separately. Your original file and saved citation excerpts stay intact, even if the upload fails. Remove the old file only after the new one is ready.")}</p><div className="ws-dialog-actions"><button className="d1-button secondary" onClick={()=>setReplacement(null)}>{t("Cancel")}</button><button className="d1-button" onClick={chooseFile}>{t("Choose corrected file")}</button></div></WorkspaceDialog>}
    {deleting && <WorkspaceDialog title={t("Delete this material?")} close={()=>setDeleting(null)} busy={Boolean(busy)}><p><strong>{deleting.name}</strong> {t("will no longer be available for search or preview. Saved citation excerpts in existing conversations will remain.")}</p>{error && <p className="ws-error" role="alert">{uiError(error)}</p>}<div className="ws-dialog-actions"><button className="d1-button secondary" disabled={Boolean(busy)} onClick={()=>setDeleting(null)}>{t("Keep file")}</button><button className="d1-button ws-danger-button" disabled={Boolean(busy)} onClick={()=>void remove()}>{busy ? t("Deleting…") : t("Delete material")}</button></div></WorkspaceDialog>}
  </div>;
}
