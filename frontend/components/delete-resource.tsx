"use client";
import {useEffect,useId,useRef,useState} from "react";
import {useRouter} from "next/navigation";
import {api} from "../lib/api";
import {useI18n,t,uiError} from "../lib/i18n";
import WorkspaceDialog from "./workspace-dialog";
import Icon from "./icon";
import "./workspace.css";

export default function DeleteResource({kind,id,name,onDeleted,destination,disabled=false,actions=[]}:{kind:"workspace"|"conversation";id:string;name:string;onDeleted?:()=>void;destination?:string;disabled?:boolean;actions?:{label:string;onSelect:()=>void}[]}) {
  useI18n();
  const router=useRouter();
  const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const running=useRef(false);
  const [menu,setMenu]=useState(false);
  const container=useRef<HTMLDivElement>(null),trigger=useRef<HTMLButtonElement>(null),menuRef=useRef<HTMLDivElement>(null);
  const menuId=useId();
  const label=kind==="workspace"?t("Delete workspace"):t("Delete conversation");
  useEffect(()=>{
    if(!menu)return;
    menuRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const outside=(event:PointerEvent)=>{if(!container.current?.contains(event.target as Node))setMenu(false);};
    document.addEventListener('pointerdown',outside);
    return ()=>document.removeEventListener('pointerdown',outside);
  },[menu]);
  async function remove() {
    if(running.current)return;
    running.current=true;setBusy(true);setError("");
    try {
      await api(kind==="workspace"?`/workspaces/${encodeURIComponent(id)}`:`/chat/sessions/${encodeURIComponent(id)}`,{method:"DELETE"});
      setOpen(false);onDeleted?.();
      if(destination)router.replace(destination);
    } catch(e) {setError(e instanceof Error?e.message:String(e));}
    finally {running.current=false;setBusy(false);}
  }
  return <><div className="ws-resource-menu" ref={container} onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node))setMenu(false);}}><button ref={trigger} type="button" className="d1-icon-button" disabled={disabled||busy} aria-label={t("Actions for {name}",{name})} aria-haspopup="menu" aria-expanded={menu} aria-controls={menu?menuId:undefined} onClick={()=>setMenu(value=>!value)} onKeyDown={e=>{if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();setMenu(true);}}}><Icon name="more"/></button>{menu&&<div ref={menuRef} id={menuId} role="menu" aria-label={t("Actions for {name}",{name})} className="ws-resource-dropdown" onKeyDown={e=>{
    if(e.key==='Escape'){e.preventDefault();setMenu(false);trigger.current?.focus();}
    else if(e.key==='Tab')setMenu(false);
    else if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){
      e.preventDefault();const items=Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('button')??[]);const index=items.indexOf(document.activeElement as HTMLButtonElement);const next=e.key==='Home'?0:e.key==='End'?items.length-1:(index+(e.key==='ArrowDown'?1:-1)+items.length)%items.length;items[next]?.focus();
    }
  }}>{actions.map(action=><button key={action.label} type="button" role="menuitem" onClick={()=>{setMenu(false);trigger.current?.focus();action.onSelect();}}>{action.label}</button>)}<button type="button" role="menuitem" className="ws-delete-resource" onClick={()=>{setMenu(false);trigger.current?.focus();setError("");setOpen(true);}}>{label}</button></div>}</div>{open&&<WorkspaceDialog title={label} close={()=>setOpen(false)} busy={busy}>
    <p><strong>{name}</strong></p><p>{kind==="workspace"?t("This permanently deletes this workspace, its materials, conversations, learning notes and recaps."):t("This permanently deletes this conversation, its messages, learning notes and recaps. Workspace materials will remain.")}</p>
    {error&&<p role="alert" className="ws-error">{uiError(error)}</p>}
    <div className="ws-dialog-actions"><button className="d1-button secondary" disabled={busy} onClick={()=>setOpen(false)}>{t("Cancel")}</button><button className="d1-button ws-danger-button" disabled={busy} onClick={()=>void remove()}>{busy?t("Deleting…"):label}</button></div>
  </WorkspaceDialog>}</>;
}
