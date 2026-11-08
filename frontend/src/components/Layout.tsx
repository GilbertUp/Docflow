import { useQuery } from "@tanstack/react-query";
import { NavLink, Outlet } from "react-router-dom";

import { api } from "../api/client";
import type { NotificationList } from "../api/types";

import { useAuth } from "../auth/AuthContext";

export function Layout() {
  const { me, can, logout } = useAuth();
  const canSeePeople = can("users.manage") || can("groups.manage");
  const notifications = useQuery({
    queryKey: ["notifications", "count"],
    queryFn: () => api.get<NotificationList>("/notifications", { query: { limit: 1 } }),
    refetchInterval: 60_000,
    retry: false,
  });
  const unread = notifications.data?.unread ?? 0;

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <NavLink className="brand" to="/files">
          DocuFlow
        </NavLink>
        <nav className="topnav" aria-label="Main">
          <NavLink to="/files">Files</NavLink>
          <NavLink to="/search">Search</NavLink>
          <NavLink to="/ask">Ask</NavLink>
          <NavLink to="/tasks">Tasks</NavLink>
          <NavLink to="/forms">Forms</NavLink>
          <NavLink to="/notifications">
            Notifications
            {unread > 0 ? (
              <span className="badge badge-warn" aria-label={`${unread} unread`}>
                {unread}
              </span>
            ) : null}
          </NavLink>
          {canSeePeople ? <NavLink to="/admin/users">Users</NavLink> : null}
          {canSeePeople ? <NavLink to="/admin/groups">Groups</NavLink> : null}
          {canSeePeople || can("security.admin") ? <NavLink to="/admin/roles">Roles</NavLink> : null}
          {can("document_types.manage") ? (
            <NavLink to="/admin/document-types">Document types</NavLink>
          ) : null}
          <NavLink to="/admin/workflows">Workflows</NavLink>
          {can("admin.operate") ? <NavLink to="/admin/operations">Operations</NavLink> : null}
          {can("audit.read") ? <NavLink to="/admin/audit">Audit trail</NavLink> : null}
          {can("retention.manage") ? <NavLink to="/admin/retention">Retention</NavLink> : null}
          {can("forms.manage") ? <NavLink to="/admin/forms">Manage forms</NavLink> : null}
          {can("integrations.manage") ? <NavLink to="/admin/api-keys">API keys</NavLink> : null}
          {can("integrations.manage") ? <NavLink to="/admin/webhooks">Webhooks</NavLink> : null}
        </nav>
        <div className="userbox">
          <NavLink to="/account">{me?.user.display_name ?? "Account"}</NavLink>
          <button type="button" className="btn" onClick={() => void logout()}>
            Sign out
          </button>
        </div>
      </header>
      <main id="main" className="content" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}
