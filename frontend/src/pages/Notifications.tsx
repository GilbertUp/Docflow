import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { DeliveryRow, NotificationList, PreferenceRow } from "../api/types";
import { EmptyState, ErrorNotice, Spinner, formatDate } from "../components/ui";

/** The preferences after one is switched, as the API takes them. */
export function withChange(
  rows: Pick<PreferenceRow, "kind" | "email">[],
  kind: string,
  email: boolean,
): Record<string, boolean> {
  return Object.fromEntries(rows.map((row) => [row.kind, row.kind === kind ? email : row.email]));
}

function EmailPreferences() {
  const queryClient = useQueryClient();
  const prefs = useQuery({
    queryKey: ["notification-preferences"],
    queryFn: () => api.get<PreferenceRow[]>("/notification-preferences"),
  });
  const sent = useQuery({
    queryKey: ["email-deliveries"],
    queryFn: () => api.get<DeliveryRow[]>("/email-deliveries"),
  });
  const save = useMutation({
    mutationFn: (input: { kind: string; email: boolean }) =>
      api.put("/notification-preferences", {
        email: withChange(prefs.data ?? [], input.kind, input.email),
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["notification-preferences"] }),
  });
  return (
    <section>
      <h2>Email me when</h2>
      <ErrorNotice error={prefs.error ?? save.error} />
      <ul className="plain-list">
        {(prefs.data ?? []).map((row) => (
          <li key={row.kind}>
            <label className="check">
              <input
                type="checkbox"
                checked={row.email}
                disabled={save.isPending}
                onChange={(event) => save.mutate({ kind: row.kind, email: event.target.checked })}
              />
              {row.description}
            </label>
          </li>
        ))}
      </ul>
      {sent.data && sent.data.length > 0 ? (
        <>
          <h3>Recent emails</h3>
          <ul className="plain-list">
            {sent.data.slice(0, 10).map((mail) => (
              <li key={mail.id}>
                {mail.subject} <span className="badge">{mail.status}</span>
                {mail.status === "failed" && mail.last_error ? (
                  <div className="muted">{mail.last_error}</div>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}

export function Notifications() {
  const queryClient = useQueryClient();
  const list = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api.get<NotificationList>("/notifications", { query: { limit: 50 } }),
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["notifications"] });
  const read = useMutation({
    mutationFn: (id: string) => api.post(`/notifications/${id}/read`, {}),
    onSuccess: refresh,
  });
  const readAll = useMutation({
    mutationFn: () => api.post("/notifications/read-all", {}),
    onSuccess: refresh,
  });
  const data = list.data;

  return (
    <div>
      <h1>Notifications</h1>
      <div className="toolbar">
        <button
          type="button"
          className="btn"
          disabled={!data || data.unread === 0 || readAll.isPending}
          onClick={() => readAll.mutate()}
        >
          Mark all as read
        </button>
      </div>
      <ErrorNotice error={list.error ?? read.error ?? readAll.error} />
      {list.isPending ? <Spinner label="Loading notifications" /> : null}
      {data && data.items.length === 0 ? (
        <EmptyState title="No notifications">Tasks and mentions for you appear here.</EmptyState>
      ) : null}
      <ul className="notification-list">
        {(data?.items ?? []).map((item) => (
          <li key={item.id} className={item.read ? "" : "unread"}>
            <Link
              to={item.kind === "mention" ? "/search" : "/tasks"}
              onClick={() => {
                if (!item.read) read.mutate(item.id);
              }}
            >
              {item.title}
            </Link>
            {item.read ? null : <span className="badge">New</span>}
            <div className="muted">{formatDate(item.created_at)}</div>
          </li>
        ))}
      </ul>
      <EmailPreferences />
    </div>
  );
}
