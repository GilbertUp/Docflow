import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { api, buildUrl } from "../../api/client";
import type { AuditEvents, AuditVerification } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import {
  EmptyState,
  ErrorNotice,
  Form,
  FormActions,
  Notice,
  Spinner,
  TextField,
  formatDate,
} from "../../components/ui";

export interface AuditFilters {
  actionPrefix: string;
  objectId: string;
  result: string;
  since: string;
  until: string;
}

export const NO_FILTERS: AuditFilters = {
  actionPrefix: "",
  objectId: "",
  result: "",
  since: "",
  until: "",
};

/** The query the API takes. Blank filters are left out, and dates cover whole days. */
export function toQuery(filters: AuditFilters): Record<string, string> {
  const query: Record<string, string> = {};
  if (filters.actionPrefix.trim()) query.action_prefix = filters.actionPrefix.trim();
  if (filters.objectId.trim()) query.object_id = filters.objectId.trim();
  if (filters.result) query.result = filters.result;
  if (filters.since) query.since = new Date(`${filters.since}T00:00:00`).toISOString();
  if (filters.until) {
    const end = new Date(`${filters.until}T00:00:00`);
    end.setDate(end.getDate() + 1);
    query.until = end.toISOString();
  }
  return query;
}

/** A sentence about a verification result. */
export function verificationText(result: AuditVerification): string {
  if (result.ok) {
    return `The trail is intact: ${result.events} events checked, ${result.checkpoints ?? 0} outside checkpoints agree.`;
  }
  if (result.reason) {
    return `The trail has been altered. The first bad event is number ${result.first_bad_seq} (${result.reason}).`;
  }
  return "The trail itself checks out, but it no longer matches a checkpoint held outside the database.";
}

export function AuditLog() {
  const { can } = useAuth();
  const allowed = can("audit.read");
  const [draft, setDraft] = useState<AuditFilters>(NO_FILTERS);
  const [applied, setApplied] = useState<AuditFilters>(NO_FILTERS);
  const events = useInfiniteQuery({
    queryKey: ["audit", applied],
    enabled: allowed,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) =>
      api.get<AuditEvents>("/audit/events", {
        query: { ...toQuery(applied), limit: 50, ...(pageParam ? { before_seq: pageParam } : {}) },
      }),
    getNextPageParam: (last) => last.next_before_seq ?? undefined,
    retry: false,
  });
  const verify = useMutation({
    mutationFn: () => api.post<AuditVerification>("/audit/verify", {}),
  });
  if (!allowed) {
    return (
      <div>
        <h1>Audit trail</h1>
        <Notice tone="info">You do not have permission to read the audit trail.</Notice>
      </div>
    );
  }
  const rows = events.data?.pages.flatMap((page) => page.events) ?? [];
  const exportUrl = buildUrl("/audit/export", toQuery(applied));

  return (
    <div>
      <h1>Audit trail</h1>
      <p className="muted">
        A record of who did what. It cannot be edited, and reading or exporting it is recorded too.
      </p>
      <Form onSubmit={() => setApplied(draft)}>
        <TextField
          label="Action starts with"
          value={draft.actionPrefix}
          onChange={(value) => setDraft({ ...draft, actionPrefix: value })}
          hint="For example document. or auth. or workflow."
        />
        <TextField
          label="About this id"
          value={draft.objectId}
          onChange={(value) => setDraft({ ...draft, objectId: value })}
        />
        <div className="field">
          <label htmlFor="audit-result">Result</label>
          <select
            id="audit-result"
            value={draft.result}
            onChange={(event) => setDraft({ ...draft, result: event.target.value })}
          >
            <option value="">Any</option>
            <option value="success">Success</option>
            <option value="failure">Failure</option>
          </select>
        </div>
        <TextField
          label="From"
          type="date"
          value={draft.since}
          onChange={(value) => setDraft({ ...draft, since: value })}
        />
        <TextField
          label="Until (included)"
          type="date"
          value={draft.until}
          onChange={(value) => setDraft({ ...draft, until: value })}
        />
        <FormActions>
          <button
            type="button"
            className="btn"
            onClick={() => {
              setDraft(NO_FILTERS);
              setApplied(NO_FILTERS);
            }}
          >
            Clear
          </button>
          <button type="submit" className="btn btn-primary">
            Search
          </button>
        </FormActions>
      </Form>
      <div className="toolbar">
        <a className="btn" href={exportUrl} download>
          Export these as CSV
        </a>
        <button
          type="button"
          className="btn"
          disabled={verify.isPending}
          onClick={() => verify.mutate()}
        >
          {verify.isPending ? "Checking…" : "Check the trail is intact"}
        </button>
      </div>
      <ErrorNotice error={verify.error} />
      {verify.data ? (
        <Notice tone={verify.data.ok ? "success" : "error"}>{verificationText(verify.data)}</Notice>
      ) : null}
      <ErrorNotice error={events.error} />
      {events.isPending ? <Spinner label="Loading the trail" /> : null}
      {events.data && rows.length === 0 ? (
        <EmptyState title="No events">Nothing matches those filters.</EmptyState>
      ) : null}
      {rows.length > 0 ? (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Audit events, newest first</caption>
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Who</th>
                <th scope="col">What</th>
                <th scope="col" className="hide-narrow">
                  About
                </th>
                <th scope="col">Result</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.seq}>
                  <td>{formatDate(row.occurred_at)}</td>
                  <td>{row.actor_name ?? row.actor_type}</td>
                  <td>
                    {row.label}
                    <div className="muted">{row.action}</div>
                  </td>
                  <td className="hide-narrow">
                    {row.object_type ?? "—"}
                    {row.object_id ? <div className="muted">{row.object_id.slice(0, 8)}</div> : null}
                  </td>
                  <td>{row.result}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {events.hasNextPage ? (
        <div className="toolbar">
          <button
            type="button"
            className="btn"
            disabled={events.isFetchingNextPage}
            onClick={() => void events.fetchNextPage()}
          >
            Older events
          </button>
        </div>
      ) : null}
    </div>
  );
}
