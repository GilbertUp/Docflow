import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, fieldErrors } from "../../api/client";
import type { Page, User } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import {
  ActionMenu,
  Dialog,
  EmptyState,
  ErrorNotice,
  Form,
  FormActions,
  Notice,
  Spinner,
  TextField,
  formatDate,
} from "../../components/ui";
import { PASSWORD_HINT } from "../PasswordChange";

const PAGE_SIZE = 25;

export function Users() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const [resetting, setResetting] = useState<User | null>(null);
  const manage = can("users.manage");

  const users = useQuery({
    queryKey: ["admin", "users", search, offset],
    queryFn: () =>
      api.get<Page<User>>("/users", { query: { search, limit: PAGE_SIZE, offset } }),
  });
  const toggle = useMutation({
    mutationFn: (user: User) =>
      api.patch(`/users/${user.id}`, {
        row_version: user.row_version,
        status: user.status === "active" ? "disabled" : "active",
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["admin", "users"] }),
  });

  return (
    <div>
      <h1>Users</h1>
      <div className="toolbar">
        <div className="field grow" style={{ margin: 0 }}>
          <label htmlFor="user-search" className="sr-only">
            Search users
          </label>
          <input
            id="user-search"
            type="text"
            placeholder="Search by name or email"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setOffset(0);
            }}
          />
        </div>
        {manage ? (
          <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
            New user
          </button>
        ) : null}
      </div>
      <ErrorNotice error={toggle.error} />

      <div className="panel">
        {users.isPending ? (
          <Spinner />
        ) : users.isError ? (
          <ErrorNotice error={users.error} />
        ) : users.data.items.length === 0 ? (
          <EmptyState title="No users found">Try a different search.</EmptyState>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <caption className="sr-only">Users</caption>
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Email</th>
                    <th scope="col">Last sign-in</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {users.data.items.map((user) => (
                    <tr key={user.id}>
                      <td>
                        {user.display_name}
                        {user.status === "disabled" ? (
                          <span className="badge badge-warn">Disabled</span>
                        ) : null}
                        {user.mfa_enabled ? <span className="badge badge-ok">MFA</span> : null}
                        {user.must_change_password ? (
                          <span className="badge">Must change password</span>
                        ) : null}
                      </td>
                      <td>{user.email}</td>
                      <td>{formatDate(user.last_login_at)}</td>
                      <td className="actions">
                        {manage ? (
                          <ActionMenu label={`Actions for ${user.display_name}`}>
                            <button type="button" onClick={() => toggle.mutate(user)}>
                              {user.status === "active" ? "Disable" : "Enable"}
                            </button>
                            <button type="button" onClick={() => setResetting(user)}>
                              Reset password…
                            </button>
                          </ActionMenu>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="toolbar">
              <span className="muted grow">
                Showing {users.data.offset + 1}–{users.data.offset + users.data.items.length} of{" "}
                {users.data.total}
              </span>
              <button
                type="button"
                className="btn"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                Previous
              </button>
              <button
                type="button"
                className="btn"
                disabled={offset + PAGE_SIZE >= users.data.total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
              </button>
            </div>
          </>
        )}
      </div>

      {creating ? <NewUserDialog onClose={() => setCreating(false)} /> : null}
      {resetting ? <ResetPasswordDialog user={resetting} onClose={() => setResetting(null)} /> : null}
    </div>
  );
}

function NewUserDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const save = useMutation({
    mutationFn: () =>
      api.post("/users", { email: email.trim(), display_name: name.trim(), password }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      void queryClient.invalidateQueries({ queryKey: ["people"] });
      onClose();
    },
  });
  const errors = fieldErrors(save.error);
  return (
    <Dialog title="New user" onClose={onClose}>
      <Form onSubmit={() => save.mutate()}>
        <TextField label="Full name" value={name} onChange={setName} autoFocus required />
        <TextField
          label="Email"
          type="email"
          value={email}
          onChange={setEmail}
          error={errors["email"]}
          autoComplete="off"
          required
        />
        <TextField
          label="Temporary password"
          type="password"
          value={password}
          onChange={setPassword}
          autoComplete="new-password"
          hint={`${PASSWORD_HINT} They must change it when they first sign in.`}
          required
        />
        <ErrorNotice error={save.error} />
        <FormActions>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={save.isPending || !email.trim() || !name.trim() || !password}
          >
            {save.isPending ? "Creating…" : "Create user"}
          </button>
        </FormActions>
      </Form>
    </Dialog>
  );
}

function ResetPasswordDialog({ user, onClose }: { user: User; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const save = useMutation({
    mutationFn: () => api.post(`/users/${user.id}/password-reset`, { password }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      onClose();
    },
  });
  return (
    <Dialog title={`Reset password for ${user.display_name}`} onClose={onClose}>
      <Form onSubmit={() => save.mutate()}>
        <Notice tone="info">
          This signs them out everywhere and makes them choose a new password at their next sign-in.
        </Notice>
        <TextField
          label="Temporary password"
          type="password"
          value={password}
          onChange={setPassword}
          autoComplete="new-password"
          hint={PASSWORD_HINT}
          autoFocus
          required
        />
        <ErrorNotice error={save.error} />
        <FormActions>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={save.isPending || !password}>
            {save.isPending ? "Resetting…" : "Reset password"}
          </button>
        </FormActions>
      </Form>
    </Dialog>
  );
}
