export type Material = { id: string; name: string; status: string; error: string | null; size_bytes: number; created_at: string; embedding_model: string };
// Reserved display contract. Existing APIs omit these fields; do not infer them from message modes.
export type Conversation = { id: string; title: string; workspace_id: string; created_at: string; updated_at: string; session_type?: "questioning" | "learning" | null; learning_status?: "active" | "finished" };
export function sessionLabel(chat: Conversation) {
  if (chat.session_type === "learning") return `Learning${chat.learning_status === "active" ? " · Active" : chat.learning_status === "finished" ? " · Finished" : ""}`;
  return chat.session_type === "questioning" ? "Questioning" : "Conversation";
}
export function fileRecovery(error: string | null) {
  const value = (error ?? "").toLowerCase();
  if (/no readable|scanned/.test(value)) return { action: "replace", message: "No readable text found. Choose a text-based PDF, Markdown or TXT file." };
  if (/encrypted/.test(value)) return { action: "replace", message: "This PDF is password-protected. Choose an unlocked, readable copy." };
  if (/worker failed|timeout|timed out|busy/.test(value)) return { action: "retry", message: "Preparation did not finish. Try processing this file again, or choose a readable replacement." };
  if (/utf|decode|invalid pdf|pdf.*invalid|eof|stream has ended|file format/.test(value)) return { action: "replace", message: "We could not read this file. Choose a readable PDF or UTF-8 Markdown or TXT file." };
  if (/maximum.*(pages|characters|chunks)/.test(value)) return { action: "replace", message: "This document is too long. Split it into smaller files and upload a readable copy." };
  if (/index is full/.test(value)) return { action: "space", message: "This workspace has reached its material capacity. Remove an unneeded file before trying again." };
  return { action: "retry", message: "Preparation did not finish. Try processing this file again, or choose a readable replacement." };
}
export function dateValue(value: string) {
  return new Date(/Z$|[+-]\d\d:\d\d$/.test(value) ? value : value + "Z").getTime();
}
export function dateText(value: string, locale = "en") {
  return new Date(dateValue(value)).toLocaleDateString(locale, { month: "short", day: "numeric", year: "numeric" });
}
export function sortMaterials(items: Material[], order: string) {
  return [...items].sort((a,b) => order === "name" ? a.name.localeCompare(b.name) || a.id.localeCompare(b.id) : (order === "oldest" ? 1 : -1) * (dateValue(a.created_at)-dateValue(b.created_at)) || a.id.localeCompare(b.id));
}
export function workspacePath(id: string) { return `/dashboard/workspaces/${encodeURIComponent(id)}`; }
export function chatPath(id: string, session?: string) {
  return `/dashboard/chat?${new URLSearchParams({workspace:id, ...(session ? {session} : {new:"1"})})}`;
}
