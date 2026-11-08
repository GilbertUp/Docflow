import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { ApprovalRow, SignatureRow } from "../../api/types";
import {
  Dialog,
  ErrorNotice,
  Form,
  FormActions,
  Notice,
  Spinner,
  TextField,
  formatDate,
} from "../../components/ui";

/** How much weight a signature carries, in words a person can act on. */
export function signatureStanding(sig: Pick<SignatureRow, "status" | "current" | "intact">): string {
  if (sig.status === "revoked") return "Revoked";
  if (!sig.intact) return "The file no longer matches what was signed";
  return sig.current ? "Valid for the current version" : "Signed an earlier version";
}

/** Approvals and signatures on a document, and a way to sign the current version. */
export function SignDialog({
  documentId,
  title,
  canSign,
  userId,
  onClose,
}: {
  documentId: string;
  title: string;
  canSign: boolean;
  userId: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const [statement, setStatement] = useState("");
  const [reason, setReason] = useState("");
  const approvals = useQuery({
    queryKey: ["approvals", documentId],
    queryFn: () => api.get<ApprovalRow[]>(`/documents/${documentId}/approvals`),
  });
  const signatures = useQuery({
    queryKey: ["signatures", documentId],
    queryFn: () => api.get<SignatureRow[]>(`/documents/${documentId}/signatures`),
  });
  const reload = () => void queryClient.invalidateQueries({ queryKey: ["signatures", documentId] });
  const sign = useMutation({
    mutationFn: () => api.post(`/documents/${documentId}/signatures`, { password, statement }),
    onSuccess: () => {
      setPassword("");
      setStatement("");
      reload();
    },
    onError: () => setPassword(""),
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api.post(`/signatures/${id}/revoke`, { reason }),
    onSuccess: () => {
      setReason("");
      reload();
    },
  });

  return (
    <Dialog title={`Approvals and signatures of ${title}`} onClose={onClose} wide>
      <h3>Approvals</h3>
      {approvals.isPending ? <Spinner label="Loading approvals" /> : null}
      <ErrorNotice error={approvals.error} />
      {approvals.data && approvals.data.length === 0 ? (
        <p className="muted">No workflow has approved or rejected this document.</p>
      ) : null}
      <ul className="plain-list">
        {(approvals.data ?? []).map((item) => (
          <li key={item.id}>
            <strong>{item.decision === "approve" ? "Approved" : "Rejected"}</strong> by{" "}
            {item.decider_name}, version {item.version_number}, {formatDate(item.created_at)}
            {item.current ? "" : " (an earlier version)"}
            {item.reason ? <div className="muted">“{item.reason}”</div> : null}
          </li>
        ))}
      </ul>

      <h3>Signatures</h3>
      {signatures.isPending ? <Spinner label="Loading signatures" /> : null}
      <ErrorNotice error={signatures.error ?? revoke.error} />
      {signatures.data && signatures.data.length === 0 ? (
        <p className="muted">Nobody has signed this document.</p>
      ) : null}
      <ul className="plain-list">
        {(signatures.data ?? []).map((item) => (
          <li key={item.id}>
            <strong>{item.signer_name}</strong>, version {item.version_number},{" "}
            {formatDate(item.created_at)}: {signatureStanding(item)}
            <div className="muted">“{item.statement}”</div>
            <div className="muted">
              Internal attestation. Fingerprint of the signed file: {item.bound_hash.slice(0, 16)}…
            </div>
            {item.status === "valid" && item.signer_id === userId ? (
              <>
                <TextField
                  label={`Reason for revoking the signature of ${item.signer_name}`}
                  value={reason}
                  onChange={setReason}
                  maxLength={500}
                />
                <button
                  type="button"
                  className="btn btn-quiet"
                  disabled={revoke.isPending || !reason.trim()}
                  onClick={() => revoke.mutate(item.id)}
                >
                  Revoke
                </button>
              </>
            ) : null}
          </li>
        ))}
      </ul>

      {canSign ? (
        <Form onSubmit={() => sign.mutate()}>
          <h3>Sign the current version</h3>
          <Notice tone="info">
            This records that you reviewed this version. It is an internal attestation, not a legally
            qualified electronic signature.
          </Notice>
          <TextField
            label="What you are confirming (optional)"
            value={statement}
            onChange={setStatement}
            maxLength={500}
          />
          <TextField
            label="Your password, to confirm it is you"
            value={password}
            onChange={setPassword}
            type="password"
            autoComplete="current-password"
          />
          <ErrorNotice error={sign.error} />
          <FormActions>
            <button type="button" className="btn" onClick={onClose}>
              Done
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={sign.isPending || !password}
            >
              Sign
            </button>
          </FormActions>
        </Form>
      ) : (
        <FormActions>
          <button type="button" className="btn" onClick={onClose}>
            Done
          </button>
        </FormActions>
      )}
    </Dialog>
  );
}
