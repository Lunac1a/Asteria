"use client";
import { useI18n, t, getLocale } from "../../../lib/i18n";

import {useEffect,useRef} from "react";
import Icon from "../../../components/icon";
import {trapDialogTab} from "../../../lib/dialog-focus";
export default function ChatPanel({title,side,close,children}:{title:string;side:"left"|"right";close:()=>void;children:React.ReactNode}) {
 useI18n();
 const ref=useRef<HTMLDialogElement>(null);
 useEffect(()=>{
  const opener=document.activeElement as HTMLElement;const dialog=ref.current;const query=matchMedia("(max-width:900px)");
  function display(){if(!dialog)return;dialog.close();if(query.matches)dialog.showModal();else dialog.show();}
  display();query.addEventListener("change",display);
  return()=>{query.removeEventListener("change",display);dialog?.close();opener?.focus({preventScroll:true});};
 },[]);
 return <dialog ref={ref} className={`chat-panel chat-panel-${side}`} aria-label={title} onCancel={e=>{if(e.target===e.currentTarget){e.preventDefault();close();}}} onKeyDown={e=>{trapDialogTab(e);if(e.key==='Escape'&&(e.target as HTMLElement).closest('dialog')===e.currentTarget){e.preventDefault();close();}}} onClick={e=>{if(e.target===e.currentTarget){const r=e.currentTarget.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)close();}}}>
 <header><h2>{title}</h2><button className="d1-icon-button" onClick={close} aria-label={t("Close {title}",{title:getLocale()==="en"?title.toLowerCase():title})} autoFocus><Icon name="close"/></button></header>{children}</dialog>;
}
