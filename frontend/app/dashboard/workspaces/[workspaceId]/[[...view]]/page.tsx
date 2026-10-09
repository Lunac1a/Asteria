import { notFound } from "next/navigation";
import WorkspacePage from "../../../../../components/workspace-page";
export default async function Page({ params }: { params: Promise<{workspaceId:string;view?:string[]}> }) {
  const {workspaceId,view=[]} = await params;
  if(view.length > 1 || (view.length && !["materials","conversations"].includes(view[0]))) notFound();
  const selected = (view[0] ?? "overview") as "overview"|"materials"|"conversations";
  return <WorkspacePage key={`${workspaceId}:${selected}`} workspaceId={workspaceId} view={selected} />;
}
