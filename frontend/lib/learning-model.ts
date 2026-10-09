export type Observation={kind:"attempt"|"question"|"correction"|"reflection";text:string;quote:string;message_id:string};
export type RecapContent={explored:string;tried:string;unclear:string;next:string};
export type Recap={id:string;cycle:number;content:RecapContent|null;revision:number;created_at:string;updated_at:string};
export type RecapDraft={id:string;cycle:number;context_revision:number;target_recap:string|null;content:RecapContent};
export type LearningState={session_id:string;status:"active"|"finished";cycle:number;revision:number;goal:string;focus:string;notes:string;next_step:string;evidence:Observation[];update_warning:boolean;finished_at:string|null;recaps:Recap[];drafts:RecapDraft[]};
export type NotesDraft=Pick<LearningState,"revision"|"goal"|"focus"|"notes"|"next_step"|"evidence">;
export const recapLabels:Record<keyof RecapContent,string>={explored:"What you explored",tried:"What you tried",unclear:"What remains unclear",next:"Where to go next"};
export function learningPath(id:string){return `/chat/sessions/${encodeURIComponent(id)}/learning`;}
export function notesDraft(data:LearningState):NotesDraft {return {revision:data.revision,goal:data.goal,focus:data.focus,notes:data.notes,next_step:data.next_step,evidence:data.evidence};}
export function readDraft<T>(key:string):T|null{try{return JSON.parse(sessionStorage.getItem(key)??"null");}catch{return null;}}
export function learningError(e:unknown){return e instanceof Error?e.message:"Could not save. Your draft is kept; please try again.";}
