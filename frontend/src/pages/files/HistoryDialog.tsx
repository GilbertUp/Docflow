import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { AuditEvent } from "../../api/types";
import { Dialog, ErrorNotice, FormActions, Spinner, formatDate } from "../../components/ui";

/** Everything recorded about one document, newest first. */
export function HistoryDialog({
  documentId,
  title,
  onClose,
}: {
  documentId: string;
  title: string;
  onClose: () => void;
}) {
  const history = useQuery({
    queryKey: ["document-audit", documentId],
    queryFn: () => api.get<AuditEvent[]>(`/documents/${documentId}/audit`),
    retry: false,
  });
  return (
    <Dialog title={`History of ${title}`} onClose={onClose} wide>
      {history.isPending ? <Spinner label="Loading the history" /> : null}
      <ErrorNotice error={history.error} />
      {history.data && history.data.length === 0 ? (
        <p className="muted">Nothing has been recorded about this document.</p>
      ) : null}
      <ol className="timeline">
        {(history.data ?? []).map((item) => (
          <li key={item.seq}>
            <strong>{item.label}</strong> by {item.actor_name ?? item.actor_type}
            <div className="muted">
              {formatDate(item.occurred_at)}
              {item.result === "success" ? "" : `, ${item.result}`}
            </div>
          </li>
        ))}
      </ol>
      <FormActions>
        <button type="button" className="btn" onClick={onClose}>
          Done
        </button>
      </FormActions>
    </Dialog>
  );
}
