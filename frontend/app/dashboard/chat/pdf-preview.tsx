"use client";
import { useI18n, t, uiError } from "../../../lib/i18n";

import { useEffect, useRef, useState } from "react";
export default function PdfPreview({ blob, pageNumber }: { blob: Blob; pageNumber: number }) {
 useI18n();
  const canvas = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let destroy: (() => void) | undefined;
    async function render() {
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
        const task = pdfjs.getDocument({ data: new Uint8Array(await blob.arrayBuffer()) });
        destroy = () => { void task.destroy(); };
        const pdf = await task.promise;
        if (cancelled) { destroy(); return; }
        const page = await pdf.getPage(pageNumber);
        if (cancelled || !canvas.current) return;
        const original = page.getViewport({ scale: 1 });
        const viewport = page.getViewport({ scale: Math.min(1.5, 2000 / Math.max(original.width, original.height)) });
        const target = canvas.current;
        target.width = viewport.width; target.height = viewport.height;
        await page.render({ canvas: target, viewport }).promise;
        if (!cancelled) setReady(true);
      } catch { if (!cancelled) setError("This PDF page could not be displayed. The saved source passage is still available above."); }
    }
    void render();
    return () => { cancelled = true; destroy?.(); };
  }, [blob, pageNumber]);
  return <>{error ? <p role="alert" className="document-error">{uiError(error)}</p> : <>{!ready && <p role="status">{t("Rendering original page…")}</p>}<div className="pdf-controls"><button className="show-answer" disabled={zoom <= 1} onClick={() => setZoom((value) => value - 0.5)} aria-label={t("Zoom out page")}>−</button><span>{Math.round(zoom * 100)}%</span><button className="show-answer" disabled={zoom >= 3} onClick={() => setZoom((value) => value + 0.5)} aria-label={t("Zoom in page")}>+</button></div><div className="pdf-canvas-scroll"><canvas ref={canvas} aria-label={t("Original document page {page}",{page:pageNumber})} role="img" style={{ width: `${zoom * 100}%`, height: "auto", maxWidth: "none" }} /></div></>}</>;
}
