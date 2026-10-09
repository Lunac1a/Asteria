"use client";
import { useI18n, t, uiError } from "../lib/i18n";

import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { documentBlob, openDocument } from "../lib/api";
import type { Material } from "../lib/workspace-model";
import WorkspaceDialog from "./workspace-dialog";

function Pdf({ blob }: { blob: Blob }) {
 useI18n();
  const canvas = useRef<HTMLCanvasElement>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const [rendering, setRendering] = useState(true);
  useEffect(() => {
    let cancelled = false;
    let destroy: (() => void) | undefined;
    void (async () => {
      const pdfjs = await import("pdfjs-dist");
      pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
      const task = pdfjs.getDocument({data: new Uint8Array(await blob.arrayBuffer())});
      destroy = () => { void task.destroy(); };
      const loaded = await task.promise;
      if (cancelled) destroy(); else setPdf(loaded);
    })().catch(() => { if (!cancelled) setError("This PDF cannot be previewed. You can download it or replace it with a readable copy."); });
    return () => { cancelled = true; destroy?.(); };
  }, [blob]);
  useEffect(() => {
    let cancelled = false;
    let cancel: (() => void) | undefined;
    if (pdf) void pdf.getPage(page).then(async result => {
      if (cancelled || !canvas.current) return;
      const original = result.getViewport({scale:1});
      const viewport = result.getViewport({scale:Math.min(1.5,1600/Math.max(original.width,original.height))});
      const target = canvas.current; target.width = viewport.width; target.height = viewport.height;
      const task = result.render({canvas:target,viewport}); cancel = () => task.cancel();
      await task.promise;
      if (!cancelled) setRendering(false);
    }).catch(() => { if (!cancelled) { setRendering(false); setError("This page could not be displayed. Download the original to view it."); } });
    return () => { cancelled = true; cancel?.(); };
  }, [pdf,page]);
  return <>{error ? <p role="alert" className="ws-error">{uiError(error)}</p> : <><div className="ws-pdf-controls"><button className="d1-button secondary" disabled={page === 1 || rendering} onClick={()=>{setRendering(true);setPage(p=>p-1);}}>{t("Previous page")}</button><span>{t("Page")}{" "}{page}{pdf ? t(" of {total}",{total:pdf.numPages}) : ""}</span><button className="d1-button secondary" disabled={!pdf || page === pdf.numPages || rendering} onClick={()=>{setRendering(true);setPage(p=>p+1);}}>{t("Next page")}</button></div>{rendering && <p role="status">{t("Rendering page…")}</p>}<canvas ref={canvas} className="ws-pdf-canvas" role="img" aria-label={t("Original PDF page {page}",{page})} /></>}</>;
}
export default function MaterialPreview({ workspaceId, material, close }: { workspaceId: string; material: Material; close: () => void }) {
 useI18n();
  const [blob, setBlob] = useState<Blob | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const pdf = material.name.toLowerCase().endsWith(".pdf");
  useEffect(() => {
    let cancelled = false;
    void documentBlob(workspaceId,material.id).then(async value => {
      const content = pdf ? "" : await value.text();
      if (!cancelled) { setBlob(value); setText(content); setError(""); }
    }).catch(e => { if(!cancelled) setError(e instanceof Error ? e.message : "Could not load the original file."); });
    return () => {cancelled=true;};
  },[workspaceId,material.id,pdf,retry]);
  return <WorkspaceDialog title={material.name} close={close} wide><div className="ws-preview-toolbar"><span>{t("Original file")}</span><button className="d1-text-button" onClick={()=>void openDocument(workspaceId,material.id,material.name).catch(e=>setError(e.message))}>{t("Download")}</button></div>{error ? <div className="ws-error" role="alert"><p>{uiError(error)}</p><button className="d1-button secondary" onClick={()=>{setError("");setRetry(v=>v+1);}}>{t("Try again")}</button></div> : !blob ? <p role="status">{t("Loading preview…")}</p> : pdf ? <Pdf blob={blob} /> : material.name.toLowerCase().endsWith(".md") ? <div className="ws-text-preview"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div> : <pre className="ws-text-preview">{text}</pre>}</WorkspaceDialog>;
}
