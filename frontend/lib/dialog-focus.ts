import type {KeyboardEvent} from "react";

/** Keep keyboard traversal in the active modal, including at its two edges. */
export function trapDialogTab(event:KeyboardEvent<HTMLDialogElement>){
 const dialog=event.currentTarget;
 if(event.key!=="Tab"||!dialog.matches(':modal')||(event.target as HTMLElement).closest('dialog')!==dialog)return;
 const items=Array.from(dialog.querySelectorAll<HTMLElement>('a[href],button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),summary,[tabindex="0"]')).filter(el=>el.getClientRects().length>0&&el.closest('dialog')===dialog);
 const first=items[0],last=items[items.length-1];
 if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
 else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
}
