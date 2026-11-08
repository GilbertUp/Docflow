import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { NotificationList } from "../api/types";
import { EmptyState, ErrorNotice, Spinner, formatDate } from "../components/ui";

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
    </div>
  );
}
