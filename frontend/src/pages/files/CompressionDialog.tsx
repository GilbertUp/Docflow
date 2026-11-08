import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { CompressionRun } from "../../api/types";
import {
  Dialog,
  ErrorNotice,
  FormActions,
  Notice,
  Spinner,
  formatBytes,
  formatDate,
} from "../../components/ui";

const STATUS: Record<string, string> = {
  accepted: "Passed the check",
  rejected: "Failed the check",
  no_benefit: "Not worth keeping",
};

/** How much smaller a copy is, as a sentence. */
export function reductionText(run: Pick<CompressionRun, "source_size" | "derivative_size" | "reduction">): string {
  if (run.reduction === null || run.reduction === undefined) return "No copy was kept.";
  const percent = Math.round(run.reduction * 100);
  return `${formatBytes(run.source_size)} down to ${formatBytes(run.derivative_size)} (${percent}% smaller)`;
}

/** A smaller copy of a file for serving and archiving. The original is always kept as it is. */
export function CompressionDialog({
  documentId,
  title,
  canRun,
  canDownload,
  onClose,
}: {
  documentId: string;
  title: string;
  canRun: boolean;
  canDownload: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [waitingFor, setWaitingFor] = useState<number | null>(null);
  const runs = useQuery({
    queryKey: ["compression", documentId],
    queryFn: () => api.get<CompressionRun[]>(`/documents/${documentId}/compression`),
    // While a copy is being made, look again every few seconds.
    refetchInterval: (query) =>
      waitingFor !== null && (query.state.data?.length ?? 0) <= waitingFor ? 3000 : false,
  });
  const run = useMutation({
    mutationFn: () => api.post(`/documents/${documentId}/compression/run`, {}),
    onSuccess: () => {
      setWaitingFor(runs.data?.length ?? 0);
      void queryClient.invalidateQueries({ queryKey: ["compression", documentId] });
    },
  });
  const working = waitingFor !== null && (runs.data?.length ?? 0) <= waitingFor;

  return (
    <Dialog title={`Smaller copy of ${title}`} onClose={onClose} wide>
      <Notice tone="info">
        The original is never changed. A copy is only offered if every page still looks like the
        original, and the worst page decides.
      </Notice>
      {runs.isPending ? <Spinner label="Loading" /> : null}
      <ErrorNotice error={runs.error ?? run.error} />
      {working ? <Spinner label="Making a smaller copy" /> : null}
      {runs.data && runs.data.length === 0 && !working ? (
        <p className="muted">No copy has been made of this document.</p>
      ) : null}
      <ul className="plain-list">
        {(runs.data ?? []).map((item) => (
          <li key={item.id}>
            <strong>Version {item.version_number}</strong>: {STATUS[item.status] ?? item.status}
            {item.recommended ? <span className="badge badge-ok">Recommended</span> : null}
            <div className="muted">
              {reductionText(item)}
              {item.worst_ssim !== null && item.worst_ssim !== undefined
                ? `. Worst page similarity ${item.worst_ssim}`
                : ""}
              . {formatDate(item.created_at)}
            </div>
            {item.reason ? <div className="muted">{item.reason}</div> : null}
            {item.pages.length > 0 ? (
              <details>
                <summary>Pages</summary>
                <ul>
                  {item.pages.map((page) => (
                    <li key={page.page}>
                      Page {page.page}: {page.kind.replace("_", " ")}, {page.treatment}, similarity{" "}
                      {page.ssim} (needs {page.threshold}){page.passed ? "" : ", failed"}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
            {item.status === "accepted" && canDownload ? (
              <a
                className="btn btn-quiet"
                href={`/api/v1/documents/${documentId}/compression/${item.id}/download`}
                download
              >
                Download copy<span className="sr-only"> of version {item.version_number}</span>
              </a>
            ) : null}
          </li>
        ))}
      </ul>
      <FormActions>
        <button type="button" className="btn" onClick={onClose}>
          Done
        </button>
        {canRun ? (
          <button
            type="button"
            className="btn btn-primary"
            disabled={run.isPending || working}
            onClick={() => run.mutate()}
          >
            Make a smaller copy
          </button>
        ) : null}
      </FormActions>
    </Dialog>
  );
}
