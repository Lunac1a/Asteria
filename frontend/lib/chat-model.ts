export type SessionType="questioning"|"learning";
export type Source={number:number;document_id:string;document_name:string;page:number|null;content:string;chunk_id:string};
export type ChatMessage={id?:string;role:"user"|"assistant";content:string;sources?:Source[];created_at?:string;answer_basis?:string};
export type ChatSession={learning_status?:"active"|"finished"|null;id:string;title:string;workspace_id:string;session_type:SessionType|null;learning_goal:string|null;updated_at:string};
export type Answer={answer:string;session_id:string;session_type:SessionType|null;learning_goal:string|null;sources:Source[];answer_basis?:string};
export type PendingTurn={uiLocale?:"en"|"zh-CN";id:string;question:string;workspace:string;session:string;type:SessionType|null};
export function validType(value:string|null):SessionType|null{return value==="questioning"||value==="learning"?value:null;}
export function conversationUrl(workspace:string,session:string){return `/dashboard/chat?${new URLSearchParams({workspace,session})}`;}
export function parsePending(value:string|null,workspace:string,session:string,type:SessionType|null):PendingTurn|null {
  try{const p=JSON.parse(value??"null");return p&&/^[0-9a-f-]{36}$/i.test(p.id)&&typeof p.question==="string"&&p.question.trim()&&p.workspace===workspace&&p.session===session&&p.type===type?p:null;}catch{return null;}
}
export function chatError(error:unknown){
 const text=error instanceof Error?error.message:"";
 if(/settings.*invalid|configure.*provider|api key|configuration|not configured/i.test(text))return "Connect a model in Settings to continue. Your message is kept here.";
 if(/invalid.*format|invalid.*citation/i.test(text))return "The model returned an incomplete response. Your message is kept; try again.";
 if(/timed out|timeout/i.test(text)&&!/search|index|embedding/i.test(text))return "The model took too long to respond. Your message is kept; try again.";
 if(/provider.*unavailable/i.test(text))return "Your model provider is unavailable. Your message is kept; try again shortly.";
 if(/search|index|embedding|materials/i.test(text))return "Couldn't read your materials. Your message is kept; try again shortly.";
 if(/already running|429/i.test(text))return "Another response is still being prepared. Try again shortly; your message is kept.";
 if(/session not found|type of an existing/i.test(text))return "This conversation could not be updated. Reload to check its saved state.";
 return "Couldn't get a response. Your message is kept. Try again to recover or complete this turn.";
}
// Presentation only: retain the original saved answer and its evidence metadata.
export function readingText(message:ChatMessage){
 if(message.role!=="assistant")return message.content;
 if(message.answer_basis==="general")return message.content.replace(/^### (?:Model knowledge \(unverified\)|模型知识（未经资料验证）)\r?\n/,"");
 if(message.answer_basis==="hybrid")return message.content.replace(/^### Document evidence\r?\n/,"### From your materials\n").replace(/\n### Model knowledge \(unverified\)\r?\n/,"\n### General explanation (not confirmed by your materials)\n");
 return message.content;
}
