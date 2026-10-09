"use client";
import { useI18n, t, uiError } from "../../../lib/i18n";

import {useEffect,useState} from "react";
import dynamic from "next/dynamic";
import {api,documentBlob,openDocument} from "../../../lib/api";
import type {Source} from "../../../lib/chat-model";
import ChatPanel from "./chat-panel";
import WorkspaceDialog from "../../../components/workspace-dialog";
import "../../../components/workspace.css";
const PdfPreview=dynamic(()=>import("./pdf-preview"),{ssr:false});
export default function SourcePanel({workspaceId,sources,initial,onClose}:{workspaceId:string;sources:Source[];initial:number;onClose:()=>void}) {
 useI18n();
 const [selected,setSelected]=useState(initial);
 const [file,setFile]=useState<{id:string;blob:Blob;text:string}|null>(null);
 const [removed,setRemoved]=useState(false);
 const [error,setError]=useState("");
 const [retry,setRetry]=useState(0);
 const [full,setFull]=useState(false);
 const source=sources[selected]??sources[0];
 useEffect(()=>{
  if(!source)return;let cancelled=false;
  void api<{id:string}[]>(`/workspaces/${encodeURIComponent(workspaceId)}/documents`).then(async docs=>{
   if(cancelled)return;
   if(!docs.some(doc=>doc.id===source.document_id)){setRemoved(true);return;}
   const blob=await documentBlob(workspaceId,source.document_id);const text=source.page?"":await blob.text();
   if(!cancelled){setFile({id:source.document_id,blob,text});setRemoved(false);setError("");}
  }).catch(()=>{if(!cancelled)setError("Couldn't open the original file. The saved excerpt is still available. Try again to check the file.");});
  return()=>{cancelled=true;};
 },[workspaceId,source,retry]);
 function choose(index:number){setSelected(index);setFile(null);setRemoved(false);setError("");setFull(false);}
 const ready=file?.id===source?.document_id?file:null;
 function original(){return ready ? source.page?<PdfPreview key={`${source.document_id}-${source.page}`} blob={ready.blob} pageNumber={source.page}/>:<pre className="chat-source-text">{ready.text}</pre>:null;}
 return <ChatPanel title={t("Sources")} side="right" close={onClose}>
 <div className="chat-panel-scroll">{source&&<>
 {sources.length>1&&<label className="chat-source-select">{t("Source")}<select value={selected} onChange={e=>choose(Number(e.target.value))}>{sources.map((s,i)=><option value={i} key={`${s.number}-${s.chunk_id}`}>{s.document_name}{s.page ? t(" · p.{page}",{page:s.page}) : t(" · excerpt")}</option>)}</select></label>}
 <h3>{source.document_name}</h3><p className="chat-caption">{source.page ? t("PDF page {page}",{page:source.page}) : t("Text excerpt")}</p>
 {removed?<p className="chat-source-removed">{t("Original file removed · saved excerpt")}</p>:error?<div className="chat-inline-error" role="alert"><p>{uiError(error)}</p><button className="d1-text-button" onClick={()=>{setError("");setRetry(v=>v+1);}}>{t("Try again")}</button></div>:ready?<div className="chat-original">{original()}</div>:<p role="status" className="chat-caption">{t("Opening original…")}</p>}
 <h4>{t("Referenced excerpt")}</h4><blockquote>{source.content}</blockquote>
 {ready&&!removed&&!error&&<div className="chat-source-actions"><button className="d1-button secondary" onClick={()=>setFull(true)}>{t("Open full preview")}</button><button className="d1-text-button" onClick={()=>void openDocument(workspaceId,source.document_id,source.document_name).catch(()=>setError("Couldn't download this file. Try again."))}>{t("Download original")}</button></div>}
 </>}</div>
 {full&&source&&<WorkspaceDialog title={`${source.document_name}${source.page?t(" · page {page}",{page:source.page}):""}`} wide close={()=>setFull(false)}>{original()}</WorkspaceDialog>}
 </ChatPanel>;
}
