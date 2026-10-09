"use client";
import { useI18n, t, uiError, getLocale } from "../../../lib/i18n";

import {Suspense,useEffect,useLayoutEffect,useRef,useState} from "react";
import {useRouter,useSearchParams} from "next/navigation";
import Link from "next/link";
import SourcePanel from "./source-panel";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {api} from "../../../lib/api";
import {chatError,readingText,conversationUrl,parsePending,validType,type Answer,type ChatMessage,type ChatSession,type PendingTurn,type Source} from "../../../lib/chat-model";
import Icon from "../../../components/icon";
import NewChatButton,{NewChatDialog} from "../../../components/new-chat";
import ChatPanel from "./chat-panel";
import "./learning.css";
import {LearningActions,LearningGoal,LearningNotes,RecapFlow,RecapHistory,FinishedComposer} from "./learning-experience";
import {learningPath,type LearningState} from "../../../lib/learning-model";
const pageSize=50;

function Chat({workspaceId,requestedSession,draftType}:{workspaceId:string;requestedSession:string;draftType:string}) {
 useI18n();
 const router=useRouter();
 const [space,setSpace]=useState<{id:string;name:string}|null>(null);
 const [session,setSession]=useState<ChatSession|null>(null);
 const [sessions,setSessions]=useState<ChatSession[]>([]);
 const [messages,setMessages]=useState<ChatMessage[]>([]);
 const [question,setQuestion]=useState("");
 const [loading,setLoading]=useState(true);
 const [loadError,setLoadError]=useState("");
 const [error,setError]=useState("");
 const [busy,setBusy]=useState(false);
 const [pendingTurn,setPendingTurn]=useState<PendingTurn|null>(null);
 const [refresh,setRefresh]=useState(0);
 const [panel,setPanel]=useState<"sessions"|"sources"|"notes"|"recaps"|"finish"|null>(null);
 const [learningState,setLearningState]=useState<LearningState|null>(null);
 const [selection,setSelection]=useState<{sources:Source[];initial:number}|null>(null);
 const [moreHistory,setMoreHistory]=useState(false);
 const [moreSessions,setMoreSessions]=useState(false);
 const [paging,setPaging]=useState(false);
 const [copied,setCopied]=useState<number|null>(null);
 const [atBottom,setAtBottom]=useState(true);
 const request=useRef<AbortController|null>(null);
 const sending=useRef(false);
 const mounted=useRef(true);
 const scroller=useRef<HTMLDivElement>(null);
 const textarea=useRef<HTMLTextAreaElement>(null);
 const scrollMode=useRef<"bottom"|number|null>("bottom");
 const storage=useRef("");
 const [draftKey,setDraftKey]=useState("");
 const type=session?.session_type??(!requestedSession?validType(draftType):null);
 const learning=type==="learning";
 const title=session?.title??(type?(learning?t("New learning session"):t("New conversation")):"New Chat");
 const label=learning?"Learning":type==="questioning"?"Questioning":"Conversation";
 const lastSources=[...messages].reverse().find(m=>m.sources?.length)?.sources??[];
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;request.current?.abort();};},[]);
 useEffect(()=>{
  let cancelled=false;
  async function load(){
   try{
    const id=workspaceId;
    if(!id&&requestedSession){const found=await api<ChatSession[]>(`/chat/sessions?session_id=${encodeURIComponent(requestedSession)}`);if(!found[0]?.workspace_id)throw Error("Conversation unavailable");router.replace(conversationUrl(found[0].workspace_id,requestedSession));return;}
    if(!id)throw Error("Choose a workspace to start a conversation.");
    const [current,chats,specific]=await Promise.all([api<{id:string;name:string}>(`/workspaces/${encodeURIComponent(id)}`),api<ChatSession[]>(`/chat/sessions?workspace_id=${encodeURIComponent(id)}&limit=${pageSize}`),requestedSession?api<ChatSession[]>(`/chat/sessions?workspace_id=${encodeURIComponent(id)}&session_id=${encodeURIComponent(requestedSession)}`):Promise.resolve([])]);
    if(requestedSession&&!specific[0])throw Error("Conversation unavailable");
    const currentType=requestedSession?specific[0].session_type:validType(draftType);
    let owner="";try{owner=JSON.parse(atob((localStorage.getItem("access_token")??"").split('.')[1].replace(/-/g,'+').replace(/_/g,'/'))).sub??"";}catch{}
    const key=`asteria.chat.v3.${owner}.${id}.${requestedSession||'draft-'+currentType}`;
    const pending=parsePending(sessionStorage.getItem(key+'.pending'),id,requestedSession,currentType);
    if(pending){const recovered=await api<Answer>(`/chat/turns/${pending.id}`).catch(()=>null);if(cancelled)return;if(recovered){sessionStorage.removeItem(key+'.pending');sessionStorage.removeItem(key);if(recovered.session_id!==requestedSession){router.replace(conversationUrl(id,recovered.session_id));return;}}}
    const history=requestedSession?await api<ChatMessage[]>(`/chat/sessions/${encodeURIComponent(requestedSession)}/messages?limit=${pageSize}`):[];
    const learningData=currentType==="learning"&&requestedSession?await api<LearningState>(learningPath(requestedSession)):null;
    if(cancelled)return;
    setLearningState(learningData);
    storage.current=key;setDraftKey(key);setSpace(current);setSessions(chats);setSession(specific[0]??null);setMessages(history);setMoreHistory(history.length===pageSize);setMoreSessions(chats.length===pageSize);
    const stillPending=parsePending(sessionStorage.getItem(key+'.pending'),id,requestedSession,currentType);
    setPendingTurn(stillPending);setQuestion(sessionStorage.getItem(key)??stillPending?.question??"");if(stillPending)setError("Your last response may still be finishing. Retry to recover it without sending a duplicate.");setLoadError("");
   }catch{if(!cancelled)setLoadError(workspaceId?"We couldn't open this conversation. It may be unavailable, or the connection may have failed.":"Choose a workspace to open or start a conversation.");}
   finally{if(!cancelled)setLoading(false);}
  }
  void load();return()=>{cancelled=true;};
 },[workspaceId,requestedSession,draftType,refresh,router]);
 useLayoutEffect(()=>{const el=scroller.current;if(!el||scrollMode.current===null)return;if(scrollMode.current==="bottom")el.scrollTop=el.scrollHeight;else el.scrollTop=el.scrollHeight-scrollMode.current;scrollMode.current=null;},[messages,loading,busy]);
 useLayoutEffect(()=>{const el=textarea.current;if(el){el.style.height="auto";el.style.height=`${Math.min(el.scrollHeight,160)}px`;}},[question,loading]);
 function updateQuestion(value:string){setQuestion(value);if(storage.current)sessionStorage.setItem(storage.current,value);}
 async function send(){
  if(learningState?.status==="finished"||sending.current||!space||(!session&&!type)||!question.trim())return;
  sending.current=true;scrollMode.current=atBottom?"bottom":null;setBusy(true);setError("");
  const turn=pendingTurn??{id:crypto.randomUUID(),question:question.trim(),workspace:space.id,session:session?.id??"",type,uiLocale:getLocale()};
  setPendingTurn(turn);sessionStorage.setItem(storage.current+'.pending',JSON.stringify(turn));sessionStorage.setItem(storage.current,turn.question);
  const controller=new AbortController();request.current=controller;const timeout=window.setTimeout(()=>controller.abort(),80000);
  try{
   const answer=await api<Answer>("/chat",{method:"POST",signal:controller.signal,body:JSON.stringify({message:turn.question,ui_locale:turn.uiLocale??null,workspace_id:turn.workspace,session_id:turn.session||null,session_type:turn.type,request_id:turn.id,answer_mode:"smart",learning_mode:"direct"})});
   if(!mounted.current)return;
   sessionStorage.removeItem(storage.current+'.pending');sessionStorage.removeItem(storage.current);setPendingTurn(null);setQuestion("");
   if(!session){router.replace(conversationUrl(space.id,answer.session_id));return;}
   scrollMode.current=atBottom?"bottom":null;
   const history=await api<ChatMessage[]>(`/chat/sessions/${encodeURIComponent(answer.session_id)}/messages?limit=${Math.min(200,Math.max(pageSize,messages.length+2))}`).catch(()=>null);
   if(!mounted.current)return;
   if(history&&messages.length<=198){setMessages(history);setMoreHistory(history.length===Math.min(200,Math.max(pageSize,messages.length+2)));}
   else setMessages(old=>[...old,{role:"user",content:turn.question},{role:"assistant",content:answer.answer,sources:answer.sources,answer_basis:answer.answer_basis}]);
   const refreshed=await api<ChatSession[]>(`/chat/sessions?workspace_id=${encodeURIComponent(space.id)}&limit=${Math.max(pageSize,Math.min(sessions.length,100))}`).catch(()=>null);
   if(mounted.current&&refreshed)setSessions(refreshed);
   if(learning)try{const state=await api<LearningState>(learningPath(answer.session_id));if(mounted.current)setLearningState(state);}catch{setError("Your response is saved, but learning notes could not be refreshed. Reload to see the latest notes.");}
   textarea.current?.focus({preventScroll:true});
  }catch(e){if(mounted.current){if(e instanceof Error && /was not saved|no answer was saved|Configure.*Settings|LLM settings.*invalid|Question cannot be blank/i.test(e.message)){setPendingTurn(null);sessionStorage.removeItem(storage.current+'.pending');}setError(controller.signal.aborted?"Stopped waiting. Your response may still be saved. Retry to recover it; your message is kept.":chatError(e));}}
  finally{window.clearTimeout(timeout);request.current=null;sending.current=false;if(mounted.current)setBusy(false);}
 }
 async function older(){
  if(!session||paging)return;setPaging(true);setError("");
  try{const rows=await api<ChatMessage[]>(`/chat/sessions/${encodeURIComponent(session.id)}/messages?offset=${messages.length}&limit=${pageSize}`);if(!mounted.current)return;scrollMode.current=(scroller.current?.scrollHeight??0)-(scroller.current?.scrollTop??0);setMessages(old=>[...rows,...old]);setMoreHistory(rows.length===pageSize);}catch{setError("Couldn't load earlier messages. Your current conversation is still here.");}finally{if(mounted.current)setPaging(false);}
 }
 async function loadSessions(){setPaging(true);try{const rows=await api<ChatSession[]>(`/chat/sessions?workspace_id=${encodeURIComponent(workspaceId)}&offset=${sessions.length}&limit=${pageSize}`);if(mounted.current){setSessions(old=>[...old,...rows.filter(row=>!old.some(s=>s.id===row.id))]);setMoreSessions(rows.length===pageSize);}}catch{setError("Couldn't load more sessions. Please try again.");}finally{if(mounted.current)setPaging(false);}}
 function openSources(sources:Source[],initial=0){setSelection({sources,initial});setPanel("sources");}
 function closePanel(){setPanel(null);}
 function changeLearning(data:LearningState){setLearningState(data);setSession(old=>old?{...old,learning_goal:data.goal,learning_status:data.status}:old);setSessions(old=>old.map(s=>s.id===data.session_id?{...s,learning_goal:data.goal,learning_status:data.status}:s));}
 const learningProps=learningState?{data:learningState,change:changeLearning,close:closePanel,draftKey:draftKey+".learning"}:null;
 if(loading)return <div className="chat-load" role="status">{t("Opening your conversation…")}</div>;
 if(loadError||!space)return <div className="chat-load"><h1>{t("Conversation unavailable")}</h1><p role="alert">{uiError(loadError)}</p><div><button className="d1-button secondary" onClick={()=>{setLoading(true);setRefresh(v=>v+1);}}>{t("Try again")}</button><Link className="d1-text-button" href={workspaceId?`/dashboard/workspaces/${encodeURIComponent(workspaceId)}`:"/dashboard/workspaces"}>{t("Back to Workspaces")}</Link></div></div>;
 return <div className={`chat-workbench ${panel?'chat-with-'+panel:''}`}>
 {panel==="sessions"&&<ChatPanel title={t("Sessions")} side="left" close={closePanel}><div className="chat-session-heading"><p>{space.name}</p><NewChatButton workspaceId={space.id} workspaceName={space.name} className="d1-button secondary" disabled={busy}/></div><nav className="chat-panel-scroll" aria-label={t("Workspace sessions")}>{!sessions.length&&<p className="chat-caption">{t("Your conversations will appear here after your first message.")}</p>}{sessions.map(s=><Link key={s.id} href={conversationUrl(space.id,s.id)} className={`chat-session ${s.session_type==="learning"?'is-learning':''}`} aria-current={s.id===session?.id?"page":undefined} onClick={()=>setPanel(null)}><span><Icon name={s.session_type==="learning"?'cap':'chat'}/></span><div><strong>{s.title}</strong><small>{s.session_type==="learning" ? t(s.learning_status==='finished'?"Learning · Finished":"Learning · Active") : s.session_type==="questioning" ? t("Questioning") : t("Conversation")}</small></div></Link>)}{moreSessions&&<button className="d1-text-button" disabled={paging} onClick={()=>void loadSessions()}>{paging ? t("Loading…") : t("Load more sessions")}</button>}</nav></ChatPanel>}
 <section className="chat-center" aria-label={t("{type} chat",{type:t(label)})}>
 <header className="chat-context"><button className="d1-text-button" aria-expanded={panel==="sessions"} onClick={()=>setPanel(panel==="sessions"?null:"sessions")}><Icon name="file" size={20}/><span>{t("Sessions")}</span></button><div className="chat-breadcrumb"><Link href={`/dashboard/workspaces/${encodeURIComponent(space.id)}`}>{space.name}</Link><span>/</span><h1 title={title}>{title}</h1></div><div className="chat-context-actions">{lastSources.length>0&&<button className="d1-text-button" aria-expanded={panel==="sources"} onClick={()=>panel==="sources"?setPanel(null):openSources(lastSources)}><Icon name="book" size={20}/><span>{t("Sources")}</span></button>}<NewChatButton workspaceId={space.id} workspaceName={space.name} className="d1-text-button" disabled={busy}/>{learningState&&<LearningActions data={learningState} open={setPanel} disabled={busy||Boolean(pendingTurn)}/>}</div></header>
 {learningState&&<LearningGoal data={learningState} open={()=>setPanel(panel==="notes"?null:"notes")}/>}
 <div className="chat-scroll" ref={scroller} onScroll={e=>{const el=e.currentTarget;setAtBottom(el.scrollHeight-el.scrollTop-el.clientHeight<100);}}>
 <div className="chat-reading">{moreHistory&&<button className="d1-text-button chat-earlier" disabled={paging} onClick={()=>void older()}>{paging ? t("Loading…") : t("Load earlier messages")}</button>}
 {!messages.length&&!busy&&<div className="chat-empty"><span className={`chat-empty-symbol ${learning?'is-learning':''}`}><Icon name={learning?'cap':'chat'} size={30}/></span><h2>{learning ? t("What would you like to learn?") : t("What are you curious about?")}</h2><p>{learning ? t("Start with a topic or a question. We’ll explore it together.") : t("Ask a question, explore an idea, or bring your materials into the conversation.")}</p><Link href={`/dashboard/workspaces/${encodeURIComponent(space.id)}/materials`} className="d1-text-button">{t("View workspace materials")}<Icon name="arrow" size={18}/></Link></div>}
 {messages.map((message,index)=><article className={`chat-message ${message.role}`} key={message.id??`saved-${index}`} aria-label={message.role==="user" ? t("Your message") : t("Asteria response")}>{message.role==="assistant"&&<span className="chat-ai-symbol" aria-hidden="true">✧</span>}<div className="chat-message-body"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{img:({alt})=><span>{alt ? `[Image: ${alt}]` : '[Image]'}</span>,a:({href,children})=><a href={href} target="_blank" rel="noopener noreferrer">{children}</a>}}>{readingText(message)}</ReactMarkdown>{message.sources?.length?<div className="chat-citations">{message.sources.map((s,i)=><button key={`${s.number}-${s.chunk_id}`} onClick={()=>openSources(message.sources!,i)}><Icon name="file" size={16}/>{s.document_name}{s.page ? t(" · p.{page}",{page:s.page}) : ''}</button>)}</div>:null}{message.role==="assistant"&&<button className="chat-copy" aria-label={t("Copy response {number}",{number:Math.floor(index/2)+1})} onClick={()=>void navigator.clipboard.writeText(message.content).then(()=>setCopied(index)).catch(()=>setError("Couldn't copy this response. Select the text to copy it."))}>{copied===index ? t("Copied") : t("Copy")}</button>}</div></article>)}
 {busy&&<><article className="chat-message user"><div className="chat-message-body"><p>{pendingTurn?.question ?? question}</p></div></article><p className="chat-working" role="status"><span className="chat-ai-symbol">✧</span>{t("Asteria is thinking…")}<button onClick={()=>request.current?.abort()} className="d1-text-button">{t("Stop waiting")}</button></p></>}
 </div></div>
 <div className="chat-composer-wrap">{!atBottom&&<button className="chat-jump" onClick={()=>{scroller.current?.scrollTo({top:scroller.current.scrollHeight,behavior:'smooth'});}}>{t("↓ Latest messages")}</button>}
 {error&&<div className="chat-inline-error" role="alert"><p>{uiError(error)}</p><div>{question.trim()&&<button className="d1-text-button" disabled={busy} onClick={()=>void send()}>{t("Retry response")}</button>}<Link className="d1-text-button" href="/dashboard/settings">{t("Open Settings")}</Link></div></div>}
 {learningState?.status==="finished"?<FinishedComposer data={learningState} change={changeLearning}/>:<><form className="chat-composer" onSubmit={e=>{e.preventDefault();void send();}}><label className="chat-sr-only" htmlFor="chat-input">{learning ? t("Your learning message") : t("Your message")}</label><textarea ref={textarea} id="chat-input" rows={1} maxLength={4000} placeholder={learning ? (messages.length ? t("Share your thoughts…") : t("What would you like to learn?")) : t("Ask anything…")} value={question} readOnly={busy||Boolean(pendingTurn)} disabled={!type&&!session} onChange={e=>updateQuestion(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing&&e.nativeEvent.keyCode!==229){e.preventDefault();void send();}}}/><div className="chat-composer-bottom"><span className={learning?'is-learning':''}><Icon name={learning?'cap':'chat'} size={18}/>{t(label)}</span><button className="chat-send" aria-label={busy ? t("Sending message") : pendingTurn ? t("Retry response") : t("Send message")} disabled={busy||!question.trim()||(!session&&!type)}><span aria-hidden="true">↑</span></button></div></form><p className="chat-keyboard-hint">{t("Enter to send · Shift + Enter for a new line")}{pendingTurn&&!busy ? t(" · Your pending message is kept for recovery.") : ''}</p></>}
 </div></section>
 {panel==="sources"&&selection&&<SourcePanel key={`${selection.sources[0]?.chunk_id}-${selection.initial}`} workspaceId={space.id} sources={selection.sources} initial={selection.initial} onClose={closePanel}/>}
 {learningProps&&panel==="notes"&&<LearningNotes {...learningProps}/>}
 {learningProps&&panel==="recaps"&&<RecapHistory {...learningProps}/>}
 {learningProps&&panel==="finish"&&<RecapFlow {...learningProps}/>}
 {!requestedSession&&!type&&<NewChatDialog workspaceId={space.id} workspaceName={space.name} onChoose={()=>{}} close={()=>router.replace(`/dashboard/workspaces/${encodeURIComponent(space.id)}`)}/>}
 </div>;
}
function ChatRoute(){
 useI18n();const query=useSearchParams();const workspace=query.get('workspace')??'';const session=query.get('session')??'';const type=query.get('type')??'';return <Chat key={`${workspace}:${session}:${type}`} workspaceId={workspace} requestedSession={session} draftType={type}/>;}
export default function Page(){
 useI18n();return <Suspense fallback={<p role="status">{t("Opening chat…")}</p>}><ChatRoute/></Suspense>;}
