import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { DocumentRetention, LegalHoldRow, RetentionClassRow } from "../../api/types";
import { Dialog, ErrorNotice, FormActions, Notice, SelectField, Spinner, formatDate } from "../../components/ui";

const STATUS: Record<string, string> = {
  not_assigned: "No retention class. It is not scheduled to be destroyed.",
  retained: "Being kept.",
  eligible: "Retention has ended. It can be destroyed when someone with that right approves.",
  held: "On legal hold. It cannot be destroyed.",
};

/** What a document's retention means right now, in a sentence. */
export function retentionSummary(status: string, eligibleAt: string | null): string {
  const base = STATUS[status] ?? status;
  if (status === "retained" && eligibleAt) return `${base} Until ${formatDate(eligibleAt)}.`;
  return base;
}

/** A document's retention class and legal holds. Changing them needs the records right. */
export function RetentionDialog({
  documentId,
  title,
  canManage,
  onClose,
}: {
  documentId: string;
  title: string;
  canManage: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [classId, setClassId] = useState("");
  const [holdId, setHoldId] = useState("");
  const state = useQuery({
    queryKey: ["doc-retention", documentId],
    queryFn: () => api.get<DocumentRetention>(`/documents/${documentId}/retention`),
  });
  const classes = useQuery({
    queryKey: ["retention-classes"],
    queryFn: () => api.get<RetentionClassRow[]>("/retention-classes"),
  });
  const holds = useQuery({
    queryKey: ["legal-holds"],
    enabled: canManage,
    queryFn: () => api.get<LegalHoldRow[]>("/legal-holds"),
  });
  const reload = () => void queryClient.invalidateQueries({ queryKey: ["doc-retention", documentId] });
  const setClass = useMutation({
    mutationFn: (value: string | null) =>
      api.put(`/documents/${documentId}/retention`, { class_id: value }),
    onSuccess: () => {
      setClassId("");
      reload();
    },
  });
  const hold = useMutation({
    mutationFn: () => api.post(`/legal-holds/${holdId}/documents`, { document_id: documentId }),
    onSuccess: () => {
      setHoldId("");
      reload();
    },
  });
  const unhold = useMutation({
    mutationFn: (id: string) => api.delete(`/legal-holds/${id}/documents/${documentId}`),
    onSuccess: reload,
  });
  const data = state.data;

  return (
    <Dialog title={`Retention of ${title}`} onClose={onClose}>
      {state.isPending ? <Spinner label="Loading retention" /> : null}
      <ErrorNotice error={state.error ?? setClass.error ?? hold.error ?? unhold.error} />
      {data ? (
        <>
          <Notice tone={data.status === "held" ? "error" : "info"}>
            {retentionSummary(data.status, data.eligible_at)}
          </Notice>
          {data.retention_class ? (
            <p>
              Kept under <strong>{data.retention_class.name}</strong>
              {data.eligible_at ? `, due ${formatDate(data.eligible_at)}` : ""}.
            </p>
          ) : null}
          {data.holds.length > 0 ? (
            <ul className="plain-list">
              {data.holds.map((item) => (
                <li key={item.id}>
                  On hold: <strong>{item.name}</strong>
                  {canManage ? (
                    <button
                      type="button"
                      className="btn btn-quiet"
                      disabled={unhold.isPending}
                      onClick={() => unhold.mutate(item.id)}
                    >
                      Take off<span className="sr-only"> {item.name}</span>
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
          {canManage ? (
            <>
              <SelectField
                label="Retention class"
                value={classId}
                onChange={setClassId}
                options={(classes.data ?? []).map((c) => ({ value: c.id, label: c.name }))}
                placeholder={data.retention_class ? "Change to…" : "Choose a class"}
              />
              <FormActions>
                {data.retention_class ? (
                  <button
                    type="button"
                    className="btn"
                    disabled={setClass.isPending}
                    onClick={() => setClass.mutate(null)}
                  >
                    Remove class
                  </button>
                ) : null}
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={!classId || setClass.isPending}
                  onClick={() => setClass.mutate(classId)}
                >
                  Set class
                </button>
              </FormActions>
              <SelectField
                label="Put on legal hold"
                value={holdId}
                onChange={setHoldId}
                options={(holds.data ?? [])
                  .filter((h) => !data.holds.some((d) => d.id === h.id))
                  .map((h) => ({ value: h.id, label: h.name }))}
                placeholder="Choose a hold"
              />
              <FormActions>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={!holdId || hold.isPending}
                  onClick={() => hold.mutate()}
                >
                  Add to hold
                </button>
              </FormActions>
            </>
          ) : null}
        </>
      ) : null}
      <FormActions>
        <button type="button" className="btn" onClick={onClose}>
          Done
        </button>
      </FormActions>
    </Dialog>
  );
}
