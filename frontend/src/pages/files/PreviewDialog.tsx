import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { AppliedStamp, PreviewInfo, StampRow } from "../../api/types";
import { Dialog, ErrorNotice, Notice, Spinner } from "../../components/ui";

const ZOOMS = [
  { label: "Fit", width: 800 },
  { label: "Large", width: 1200 },
  { label: "Largest", width: 1800 },
];

/** Address of a page image. The server renders it, so the file itself never reaches the browser. */
export function pageUrl(
  documentId: string,
  number: number,
  width: number,
  nonce?: number,
): string {
  const base = `/api/v1/documents/${documentId}/preview/pages/${number}?width=${width}`;
  return nonce === undefined ? base : `${base}&v=${nonce}`;
}

/** Where a click landed on an image, as shares of its width and height from the top left. */
export function shareOf(
  click: { x: number; y: number },
  box: { left: number; top: number; width: number; height: number },
): { x: number; y: number } {
  const clamp = (value: number) => Math.min(1, Math.max(0, value));
  return {
    x: clamp((click.x - box.left) / Math.max(1, box.width)),
    y: clamp((click.y - box.top) / Math.max(1, box.height)),
  };
}

/** Clamps a page number into the pages that exist. */
export function clampPage(number: number, total: number): number {
  return Math.min(Math.max(1, number), Math.max(1, total));
}

/** A document shown page by page, with thumbnails to jump between pages. */
export function PreviewDialog({
  documentId,
  title,
  canStamp = false,
  onClose,
}: {
  documentId: string;
  title: string;
  canStamp?: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [stampId, setStampId] = useState("");
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(0);
  const info = useQuery({
    queryKey: ["preview", documentId],
    queryFn: () => api.get<PreviewInfo>(`/documents/${documentId}/preview`),
    retry: false,
  });
  const stamps = useQuery({
    queryKey: ["stamps"],
    enabled: canStamp,
    queryFn: () => api.get<StampRow[]>("/stamps"),
  });
  const applied = useQuery({
    queryKey: ["applied-stamps", documentId],
    queryFn: () => api.get<AppliedStamp[]>(`/documents/${documentId}/stamps`),
  });
  const reloadStamps = () =>
    void queryClient.invalidateQueries({ queryKey: ["applied-stamps", documentId] });
  const place = useMutation({
    mutationFn: (where: { x: number; y: number }) =>
      api.post(`/documents/${documentId}/stamps`, { stamp_id: stampId, page, ...where }),
    onSuccess: reloadStamps,
  });
  const unplace = useMutation({
    mutationFn: (id: string) => api.delete(`/documents/${documentId}/stamps/${id}`),
    onSuccess: reloadStamps,
  });
  const total = info.data?.pages.length ?? 0;
  const current = clampPage(page, total);
  const width = ZOOMS[zoom]?.width ?? 800;
  const here = (applied.data ?? []).filter((item) => item.page === page && item.current);
  const nonce = (applied.data ?? []).length;

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
          {canStamp ? (
            <div className="toolbar">
              <label>
                Stamp{" "}
                <select value={stampId} onChange={(event) => setStampId(event.target.value)}>
                  <option value="">None, just look</option>
                  {(stamps.data ?? []).map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.text}
                    </option>
                  ))}
                </select>
              </label>
              {stampId ? <span className="muted">Click the page where it should go.</span> : null}
            </div>
          ) : null}
          <ErrorNotice error={place.error ?? unplace.error} />
          {here.length > 0 ? (
            <ul className="plain-list">
              {here.map((item) => (
                <li key={item.id}>
                  {item.text} on this page, by {item.applied_by}
                  <button
                    type="button"
                    className="btn btn-quiet"
                    disabled={unplace.isPending}
                    onClick={() => unplace.mutate(item.id)}
                  >
                    Remove<span className="sr-only"> the {item.text} stamp</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
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
                key={`${current}-${width}-${nonce}`}
                src={pageUrl(documentId, current, width, nonce)}
                alt={`Page ${current} of ${title}`}
                className={stampId ? "placing" : undefined}
                onClick={(event) => {
                  if (!stampId) return;
                  const box = event.currentTarget.getBoundingClientRect();
                  place.mutate(
                    shareOf({ x: event.clientX, y: event.clientY }, box),
                  );
                }}
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
