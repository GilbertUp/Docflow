import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";

import { useAuth } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { Spinner } from "./components/ui";
import { Account } from "./pages/Account";
import { ApiKeys } from "./pages/admin/ApiKeys";
import { Groups } from "./pages/admin/Groups";
import { DocumentTypes } from "./pages/admin/DocumentTypes";
import { Roles } from "./pages/admin/Roles";
import { Users } from "./pages/admin/Users";
import { Workflows } from "./pages/admin/Workflows";
import { Browser } from "./pages/files/Browser";
import { CabinetsOverview, FilesLayout } from "./pages/files/FilesLayout";
import { Login } from "./pages/Login";
import { ForcedPasswordChange } from "./pages/PasswordChange";
import { Notifications } from "./pages/Notifications";
import { Search } from "./pages/Search";
import { Tasks } from "./pages/Tasks";

/** Everything except the sign-in page needs a session, and a new user must pick a password. */
function RequireAuth() {
  const { status, me, signedOutByUser } = useAuth();
  const location = useLocation();
  if (status === "loading") return <Spinner label="Checking your session" />;
  if (status !== "authenticated") {
    // Remember where an expired session was, but not after someone chose to sign out: the next
    // person to sign in on this computer must not land on the last person's page.
    const state = signedOutByUser ? undefined : { from: location.pathname + location.search };
    return <Navigate to="/login" replace state={state} />;
  }
  if (me?.user.must_change_password) return <ForcedPasswordChange />;
  return <Outlet />;
}

function NotFound() {
  return (
    <div className="panel">
      <h1>Page not found</h1>
      <p className="muted">That page does not exist.</p>
    </div>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/files" replace />} />
          <Route path="files" element={<FilesLayout />}>
            <Route index element={<CabinetsOverview />} />
            <Route path="cabinets/:id" element={<Browser key="cabinet" kind="cabinet" />} />
            <Route path="folders/:id" element={<Browser key="folder" kind="folder" />} />
          </Route>
          <Route path="search" element={<Search />} />
          <Route path="tasks" element={<Tasks />} />
          <Route path="notifications" element={<Notifications />} />
          <Route path="admin/users" element={<Users />} />
          <Route path="admin/groups" element={<Groups />} />
          <Route path="admin/roles" element={<Roles />} />
          <Route path="admin/document-types" element={<DocumentTypes />} />
          <Route path="admin/workflows" element={<Workflows />} />
          <Route path="admin/api-keys" element={<ApiKeys />} />
          <Route path="account" element={<Account />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Route>
    </Routes>
  );
}
