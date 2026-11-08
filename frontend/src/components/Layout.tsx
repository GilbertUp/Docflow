import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

export function Layout() {
  const { me, can, logout } = useAuth();
  const canSeePeople = can("users.manage") || can("groups.manage");

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
          {canSeePeople ? <NavLink to="/admin/users">Users</NavLink> : null}
          {canSeePeople ? <NavLink to="/admin/groups">Groups</NavLink> : null}
          {canSeePeople || can("security.admin") ? <NavLink to="/admin/roles">Roles</NavLink> : null}
          {can("document_types.manage") ? (
            <NavLink to="/admin/document-types">Document types</NavLink>
          ) : null}
          {can("integrations.manage") ? <NavLink to="/admin/api-keys">API keys</NavLink> : null}
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
