"use client";
import { useI18n, t } from "../lib/i18n";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import Icon from "./icon";
import {trapDialogTab} from "../lib/dialog-focus";
import "./new-chat.css";

export function NewChatDialog({workspaceId,workspaceName,close,onChoose=close}:{workspaceId:string;workspaceName:string;close:()=>void;onChoose?:()=>void}) {
 useI18n();
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const opener=document.activeElement as HTMLElement;const dialog=ref.current;dialog?.showModal();return()=>{dialog?.close();opener?.focus();};},[]);
  return <dialog className="new-chat-dialog" ref={ref} aria-labelledby="new-chat-title" onKeyDown={trapDialogTab} onCancel={e=>{e.preventDefault();close();}} onClick={e=>{if(e.target===e.currentTarget)close();}}>
    <div className="new-chat-body"><div className="new-chat-context"><span>{workspaceName}</span><button className="d1-icon-button" onClick={close} aria-label={t("Close new chat")}><Icon name="close"/></button></div>
    <h2 id="new-chat-title">{t("What would you like to do?")}</h2><p>{t("Choose how you&apos;d like to spend this session.")}</p>
    <div className="new-chat-options">{(["questioning","learning"] as const).map(type=><Link key={type} href={`/dashboard/chat?${new URLSearchParams({workspace:workspaceId,new:"1",type})}`} onClick={onChoose} className={`new-chat-option ${type}`}><span className="new-chat-symbol"><Icon name={type==="learning"?"cap":"chat"} size={28}/></span><span className="new-chat-type">{type==="learning" ? t("Learning") : t("Questioning")}</span><strong>{type==="learning" ? t("Start learning") : t("Ask a question")}</strong><span>{type==="learning" ? t("Work through a topic, ask follow-up questions and explore ideas together.") : t("Get answers, explore ideas and discuss freely.")}</span><Icon name="arrow"/></Link>)}</div></div>
    <footer><button className="d1-text-button" onClick={close}>{t("Cancel")}</button><span>{t("Your conversation starts when you send your first message.")}</span></footer>
  </dialog>;
}
export default function NewChatButton({workspaceId,workspaceName,className="d1-button",disabled=false}:{workspaceId:string;workspaceName:string;className?:string;disabled?:boolean}) {
 useI18n();
  const [open,setOpen]=useState(false);
  return <><button className={className} disabled={disabled} onClick={()=>setOpen(true)}><Icon name="plus" size={18}/>{t("New Chat")}</button>{open&&<NewChatDialog workspaceId={workspaceId} workspaceName={workspaceName} close={()=>setOpen(false)}/>}</>;
}
