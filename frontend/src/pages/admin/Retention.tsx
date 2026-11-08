import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type {
  DisposalCandidate,
  DisposalRecord,
  LegalHoldRow,
  RetentionClassRow,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import {
  Dialog,
  EmptyState,
  ErrorNotice,
  Form,
  FormActions,
  Notice,
  SelectField,
  Spinner,
  TextField,
  formatDate,
} from "../../components/ui";

/** "7 years" or "30 days", for a retention period given in days. */
export function describeDays(days: number): string {
  if (days % 365 === 0) {
    const years = days / 365;
    return `${years} ${years === 1 ? "year" : "years"}`;
  }
  return `${days} ${days === 1 ? "day" : "days"}`;
}

/** Days from a number and a unit chosen on the form. */
export function toDays(amount: string, unit: "days" | "years"): number {
  const n = Number(amount);
  return unit === "years" ? Math.round(n * 365) : Math.round(n);
}

const TRIGGERS = [
  { value: "created", label: "When the document was filed" },
  { value: "last_version", label: "When its newest version was added" },
];

function Classes({ manage }: { manage: boolean }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("7");
  const [unit, setUnit] = useState<"days" | "years">("years");
  const [trigger, setTrigger] = useState("created");
  const list = useQuery({
    queryKey: ["retention-classes"],
    queryFn: () =>
      api.get<RetentionClassRow[]>("/retention-classes", { query: { include_archived: true } }),
  });
  const reload = () => void queryClient.invalidateQueries({ queryKey: ["retention-classes"] });
  const create = useMutation({
    mutationFn: () =>
      api.post("/retention-classes", {
        name: name.trim(),
        duration_days: toDays(amount, unit),
        trigger,
      }),
    onSuccess: () => {
      setName("");
      reload();
    },
  });
  const toggle = useMutation({
    mutationFn: (row: RetentionClassRow) =>
      api.patch(`/retention-classes/${row.id}`, {
        status: row.status === "active" ? "archived" : "active",
      }),
    onSuccess: reload,
  });
  return (
    <section>
      <h2>Retention classes</h2>
      <p className="muted">How long a kind of document must be kept before it can be destroyed.</p>
      <ErrorNotice error={list.error ?? toggle.error} />
      {list.data && list.data.length === 0 ? (
        <p className="muted">No retention classes yet.</p>
      ) : null}
      <ul className="plain-list">
        {(list.data ?? []).map((row) => (
          <li key={row.id}>
            <strong>{row.name}</strong>: keep {describeDays(row.duration_days)} from{" "}
            {row.trigger === "created" ? "filing" : "the newest version"}
            {row.status === "archived" ? <span className="badge badge-warn">Archived</span> : null}
            {manage ? (
              <button type="button" className="btn btn-quiet" onClick={() => toggle.mutate(row)}>
                {row.status === "active" ? "Archive" : "Restore"}
                <span className="sr-only"> {row.name}</span>
              </button>
            ) : null}
          </li>
        ))}
      </ul>
      {manage ? (
        <Form onSubmit={() => create.mutate()}>
          <TextField label="Class name" value={name} onChange={setName} maxLength={120} />
          <TextField label="Keep for" value={amount} onChange={setAmount} inputMode="numeric" />
          <SelectField
            label="Unit"
            value={unit}
            onChange={(value) => setUnit(value as "days" | "years")}
            options={[
              { value: "years", label: "Years" },
              { value: "days", label: "Days" },
            ]}
          />
          <SelectField
            label="The clock starts"
            value={trigger}
            onChange={setTrigger}
            options={TRIGGERS}
          />
          <ErrorNotice error={create.error} />
          <FormActions>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={create.isPending || !name.trim() || !amount}
            >
              Add class
            </button>
          </FormActions>
        </Form>
      ) : null}
    </section>
  );
}

function Holds() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [reason, setReason] = useState("");
  const [releasing, setReleasing] = useState<LegalHoldRow | null>(null);
  const [why, setWhy] = useState("");
  const list = useQuery({
    queryKey: ["legal-holds"],
    queryFn: () => api.get<LegalHoldRow[]>("/legal-holds", { query: { include_released: true } }),
  });
  const reload = () => void queryClient.invalidateQueries({ queryKey: ["legal-holds"] });
  const create = useMutation({
    mutationFn: () => api.post("/legal-holds", { name: name.trim(), reason: reason.trim() }),
    onSuccess: () => {
      setName("");
      setReason("");
      reload();
    },
  });
  const release = useMutation({
    mutationFn: (row: LegalHoldRow) => api.post(`/legal-holds/${row.id}/release`, { reason: why }),
    onSuccess: () => {
      setReleasing(null);
      setWhy("");
      reload();
    },
  });
  return (
    <section>
      <h2>Legal holds</h2>
      <p className="muted">
        While a hold is active, nothing on it can be destroyed. Add documents to a hold from the
        Retention button on each document.
      </p>
      <ErrorNotice error={list.error} />
      <ul className="plain-list">
        {(list.data ?? []).map((row) => (
          <li key={row.id}>
            <strong>{row.name}</strong> ({row.documents}{" "}
            {row.documents === 1 ? "document" : "documents"}): {row.reason}
            {row.status === "released" ? (
              <span className="badge">Released: {row.release_reason}</span>
            ) : (
              <button type="button" className="btn btn-quiet" onClick={() => setReleasing(row)}>
                Release<span className="sr-only"> {row.name}</span>
              </button>
            )}
          </li>
        ))}
      </ul>
      <Form onSubmit={() => create.mutate()}>
        <TextField label="Hold name" value={name} onChange={setName} maxLength={120} />
        <TextField label="Why is it needed" value={reason} onChange={setReason} maxLength={1000} />
        <ErrorNotice error={create.error} />
        <FormActions>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={create.isPending || !name.trim() || !reason.trim()}
          >
            Start hold
          </button>
        </FormActions>
      </Form>
      {releasing ? (
        <Dialog title={`Release ${releasing.name}`} onClose={() => setReleasing(null)}>
          <Form onSubmit={() => release.mutate(releasing)}>
            <Notice tone="info">
              Documents on this hold can be destroyed again if nothing else holds them and their
              retention has ended.
            </Notice>
            <TextField label="Why is it being released" value={why} onChange={setWhy} maxLength={1000} />
            <ErrorNotice error={release.error} />
            <FormActions>
              <button type="button" className="btn" onClick={() => setReleasing(null)}>
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={release.isPending || !why.trim()}
              >
                Release hold
              </button>
            </FormActions>
          </Form>
        </Dialog>
      ) : null}
    </section>
  );
}

function Disposal() {
  const queryClient = useQueryClient();
  const [target, setTarget] = useState<DisposalCandidate | null>(null);
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState("");
  const candidates = useQuery({
    queryKey: ["disposal-candidates"],
    queryFn: () => api.get<DisposalCandidate[]>("/disposal/candidates"),
  });
  const record = useQuery({
    queryKey: ["disposal-events"],
    queryFn: () => api.get<DisposalRecord[]>("/disposal/events"),
  });
  const dispose = useMutation({
    mutationFn: (item: DisposalCandidate) =>
      api.post(`/documents/${item.document_id}/dispose`, { reason }),
    onSuccess: () => {
      setTarget(null);
      setReason("");
      setTyped("");
      void queryClient.invalidateQueries({ queryKey: ["disposal-candidates"] });
      void queryClient.invalidateQueries({ queryKey: ["disposal-events"] });
    },
  });
  return (
    <section>
      <h2>Ready to be destroyed</h2>
      <p className="muted">
        Documents past their retention that nothing holds, and that you may approve for disposal.
      </p>
      <ErrorNotice error={candidates.error} />
      {candidates.isPending ? <Spinner label="Looking for documents" /> : null}
      {candidates.data && candidates.data.length === 0 ? (
        <EmptyState title="Nothing is due">No document is waiting for disposal.</EmptyState>
      ) : null}
      <ul className="plain-list">
        {(candidates.data ?? []).map((item) => (
          <li key={item.document_id}>
            <strong>{item.title}</strong>, {item.retention_class}, due {formatDate(item.eligible_at)}
            <button type="button" className="btn btn-quiet" onClick={() => setTarget(item)}>
              Dispose<span className="sr-only"> of {item.title}</span>
            </button>
          </li>
        ))}
      </ul>
      {target ? (
        <Dialog title={`Destroy ${target.title}`} onClose={() => setTarget(null)}>
          <Form onSubmit={() => dispose.mutate(target)}>
            <Notice tone="error">
              This destroys the document, every version, its comments, tasks and signatures, and
              the stored files. It cannot be undone. A permanent record of it is kept.
            </Notice>
            <TextField label="Reason (optional)" value={reason} onChange={setReason} maxLength={1000} />
            <TextField
              label={`Type DESTROY to confirm`}
              value={typed}
              onChange={setTyped}
              autoComplete="off"
            />
            <ErrorNotice error={dispose.error} />
            <FormActions>
              <button type="button" className="btn" onClick={() => setTarget(null)}>
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={dispose.isPending || typed !== "DESTROY"}
              >
                Destroy permanently
              </button>
            </FormActions>
          </Form>
        </Dialog>
      ) : null}

      <h2>Disposal record</h2>
      <ErrorNotice error={record.error} />
      {record.data && record.data.length === 0 ? (
        <p className="muted">Nothing has been destroyed.</p>
      ) : null}
      <ul className="plain-list">
        {(record.data ?? []).map((item) => (
          <li key={item.id}>
            <strong>{item.title}</strong> ({item.versions}{" "}
            {item.versions === 1 ? "version" : "versions"}), {item.retention_class},{" "}
            {formatDate(item.created_at)}
            {item.reason ? <div className="muted">“{item.reason}”</div> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function Retention() {
  const { can } = useAuth();
  const manage = can("retention.manage");
  return (
    <div>
      <h1>Retention</h1>
      <Classes manage={manage} />
      {manage ? <Holds /> : null}
      {manage ? <Disposal /> : null}
    </div>
  );
}
