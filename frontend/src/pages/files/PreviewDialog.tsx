import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { PreviewInfo } from "../../api/types";
import { Dialog, ErrorNotice, Notice, Spinner } from "../../components/ui";

const ZOOMS = [
  { label: "Fit", width: 800 },
  { label: "Large", width: 1200 },
  { label: "Largest", width: 1800 },
];

/** Address of a page image. The server renders it, so the file itself never reaches the browser. */
export function pageUrl(documentId: string, number: number, width: number): string {
  return `/api/v1/documents/${documentId}/preview/pages/${number}?width=${width}`;
}

/** Clamps a page number into the pages that exist. */
export function clampPage(number: number, total: number): number {
  return Math.min(Math.max(1, number), Math.max(1, total));
}

/** A document shown page by page, with thumbnails to jump between pages. */
export function PreviewDialog({
  documentId,
  title,
  onClose,
}: {
  documentId: string;
  title: string;
  onClose: () => void;
}) {
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(0);
  const info = useQuery({
    queryKey: ["preview", documentId],
    queryFn: () => api.get<PreviewInfo>(`/documents/${documentId}/preview`),
    retry: false,
  });
  const total = info.data?.pages.length ?? 0;
  const current = clampPage(page, total);
  const width = ZOOMS[zoom]?.width ?? 800;

  return (
    <Dialog title={`View ${title}`} onClose={onClose} wide>
      {info.isPending ? <Spinner label="Loading the preview" /> : null}
      <ErrorNotice error={info.error} />
      {info.data?.kind === "text" ? (
        <Notice tone="info">
          This file is plain text. Use the Text view to read it.
        </Notice>
      ) : null}
      {info.data?.kind === "none" ? (
        <Notice tone="info">This kind of file cannot be shown in the browser yet.</Notice>
      ) : null}
      {info.data?.kind === "pages" && total > 0 ? (
        <div className="preview">
          <div className="toolbar">
            <button
              type="button"
              className="btn"
              disabled={current <= 1}
              onClick={() => setPage(current - 1)}
            >
              Previous page
            </button>
            <span aria-live="polite">
              Page {current} of {total}
            </span>
            <button
              type="button"
              className="btn"
              disabled={current >= total}
              onClick={() => setPage(current + 1)}
            >
              Next page
            </button>
            <span className="grow" />
            <label>
              Size{" "}
              <select value={zoom} onChange={(event) => setZoom(Number(event.target.value))}>
                {ZOOMS.map((item, index) => (
                  <option key={item.label} value={index}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="preview-body">
            {total > 1 ? (
              <nav aria-label="Pages" className="preview-thumbs">
                {info.data.pages.map((item) => (
                  <button
                    key={item.number}
                    type="button"
                    className="preview-thumb"
                    aria-label={`Go to page ${item.number}`}
                    aria-current={item.number === current ? "page" : undefined}
                    onClick={() => setPage(item.number)}
                  >
                    <img
                      src={pageUrl(documentId, item.number, 200)}
                      alt=""
                      loading="lazy"
                      width={100}
                    />
                  </button>
                ))}
              </nav>
            ) : null}
            <div className="preview-page">
              <img
                key={`${current}-${width}`}
                src={pageUrl(documentId, current, width)}
                alt={`Page ${current} of ${title}`}
              />
            </div>
          </div>
        </div>
      ) : null}
      {info.data?.kind === "pages" && total === 0 ? (
        <Notice tone="info">This file has no pages to show.</Notice>
      ) : null}
    </Dialog>
  );
}
