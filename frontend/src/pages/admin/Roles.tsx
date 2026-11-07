import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import { type Page, type Role, type RoleHolders, SYSTEM_PERMISSIONS } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import {
  ActionMenu,
  Dialog,
  EmptyState,
  ErrorNotice,
  Form,
  FormActions,
  Notice,
  SelectField,
  Spinner,
  TextField,
} from "../../components/ui";
import { usePeople } from "../../lib/people";

const PERMISSION_LABEL = Object.fromEntries(SYSTEM_PERMISSIONS);

type DialogState =
  | { kind: "create" }
  | { kind: "edit"; role: Role }
  | { kind: "holders"; role: Role }
  | null;

export function Roles() {
  const { can } = useAuth();
  const [dialog, setDialog] = useState<DialogState>(null);
  const roles = useQuery({
    queryKey: ["admin", "roles"],
    queryFn: () => api.get<Page<Role>>("/roles", { query: { limit: 200 } }),
  });
  const admin = can("security.admin");

  return (
    <div>
      <h1>Roles</h1>
      <p className="muted">
        A role is a named set of administrative powers. Give a role to people or groups; changes
        apply on their very next request.
      </p>
      <div className="toolbar">
        {admin ? (
          <button type="button" className="btn btn-primary" onClick={() => setDialog({ kind: "create" })}>
            New role
          </button>
        ) : null}
      </div>
      <div className="panel">
        {roles.isPending ? (
          <Spinner />
        ) : roles.isError ? (
          <ErrorNotice error={roles.error} />
        ) : roles.data.items.length === 0 ? (
          <EmptyState title="No roles yet" />
        ) : (
          <div className="table-wrap">
            <table>
              <caption className="sr-only">Roles</caption>
              <thead>
                <tr>
                  <th scope="col">Role</th>
                  <th scope="col">Powers</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {roles.data.items.map((role) => (
                  <tr key={role.id}>
                    <td>
                      <strong>{role.name}</strong>
                      {role.description ? <div className="muted">{role.description}</div> : null}
                    </td>
                    <td>
                      {role.permissions.length === 0 ? (
                        <span className="muted">None</span>
                      ) : (
                        role.permissions.map((code) => (
                          <span key={code} className="badge" title={PERMISSION_LABEL[code]}>
                            {code}
                          </span>
                        ))
                      )}
                    </td>
                    <td className="actions">
                      <ActionMenu label={`Actions for ${role.name}`}>
                        <button type="button" onClick={() => setDialog({ kind: "holders", role })}>
                          Who has this role…
                        </button>
                        {admin ? (
                          <button type="button" onClick={() => setDialog({ kind: "edit", role })}>
                            Edit powers…
                          </button>
                        ) : null}
                      </ActionMenu>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {dialog?.kind === "create" ? <RoleDialog onClose={() => setDialog(null)} /> : null}
      {dialog?.kind === "edit" ? (
        <RoleDialog role={dialog.role} onClose={() => setDialog(null)} />
      ) : null}
      {dialog?.kind === "holders" ? (
        <HoldersDialog role={dialog.role} onClose={() => setDialog(null)} />
      ) : null}
    </div>
  );
}

function RoleDialog({ role, onClose }: { role?: Role; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(role?.name ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [chosen, setChosen] = useState<Set<string>>(new Set(role?.permissions ?? []));
  const save = useMutation({
    mutationFn: () =>
      role
        ? api.put(`/roles/${role.id}/permissions`, { permissions: [...chosen] })
        : api.post("/roles", {
            name: name.trim(),
            description: description.trim(),
            permissions: [...chosen],
          }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "roles"] });
      void queryClient.invalidateQueries({ queryKey: ["people"] });
      onClose();
    },
  });

  function flip(code: string, on: boolean) {
    const next = new Set(chosen);
    if (on) next.add(code);
    else next.delete(code);
    setChosen(next);
  }

  return (
    <Dialog title={role ? `Powers of ${role.name}` : "New role"} onClose={onClose} wide>
      <Form onSubmit={() => save.mutate()}>
        {role ? null : (
          <>
            <TextField label="Role name" value={name} onChange={setName} autoFocus required />
            <TextField label="Description" value={description} onChange={setDescription} />
          </>
        )}
        <fieldset className="checks" style={{ border: 0, padding: 0 }}>
          <legend>Powers</legend>
          {SYSTEM_PERMISSIONS.map(([code, text]) => (
            <label key={code}>
              <input
                type="checkbox"
                checked={chosen.has(code)}
                onChange={(event) => flip(code, event.target.checked)}
              />
              <span>
                <strong>{code}</strong>
                <span className="muted"> — {text}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <Notice tone="info">
          You can only grant powers you hold yourself, and the organization must always keep at
          least one security administrator.
        </Notice>
        <ErrorNotice error={save.error} />
        <FormActions>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={save.isPending || (!role && !name.trim())}
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
        </FormActions>
      </Form>
    </Dialog>
  );
}

function HoldersDialog({ role, onClose }: { role: Role; onClose: () => void }) {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const people = usePeople();
  const [type, setType] = useState<"user" | "group">("user");
  const [picked, setPicked] = useState("");
  const holders = useQuery({
    queryKey: ["admin", "holders", role.id],
    queryFn: () => api.get<RoleHolders>(`/roles/${role.id}/assignments`),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["admin", "holders", role.id] });
  };
  const body = (id: string, kind: "user" | "group") =>
    kind === "user" ? { user_id: id } : { group_id: id };
  const add = useMutation({
    mutationFn: () => api.put(`/roles/${role.id}/assignments`, body(picked, type)),
    onSuccess: () => {
      setPicked("");
      refresh();
    },
  });
  const remove = useMutation({
    mutationFn: ({ id, kind }: { id: string; kind: "user" | "group" }) =>
      api.post(`/roles/${role.id}/assignments/remove`, body(id, kind)),
    onSuccess: refresh,
  });
  const canGive = type === "user" ? can("users.manage") : can("groups.manage");
  const canChange = can("users.manage") || can("groups.manage");

  return (
    <Dialog title={`Who has ${role.name}`} onClose={onClose}>
      {holders.isPending ? <Spinner /> : null}
      <ErrorNotice error={holders.error} />
      {holders.data ? (
        <>
          <ul>
            {holders.data.groups.map((group) => (
              <li key={group.id}>
                {group.name} <span className="badge">Group</span>{" "}
                {can("groups.manage") ? (
                  <button
                    type="button"
                    className="btn btn-quiet"
                    onClick={() => remove.mutate({ id: group.id, kind: "group" })}
                  >
                    Remove<span className="sr-only"> {group.name}</span>
                  </button>
                ) : null}
              </li>
            ))}
            {holders.data.users.map((user) => (
              <li key={user.id}>
                {user.display_name} <span className="muted">({user.email})</span>{" "}
                {can("users.manage") ? (
                  <button
                    type="button"
                    className="btn btn-quiet"
                    onClick={() => remove.mutate({ id: user.id, kind: "user" })}
                  >
                    Remove<span className="sr-only"> {user.display_name}</span>
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
          {holders.data.users.length + holders.data.groups.length === 0 ? (
            <EmptyState title="Nobody has this role" />
          ) : null}
          {canChange ? (
            <div className="acl-grid">
              <SelectField
                label="Give it to"
                value={type}
                onChange={(value) => {
                  setType(value as "user" | "group");
                  setPicked("");
                }}
                options={[
                  { value: "user", label: "A person" },
                  { value: "group", label: "A group" },
                ]}
              />
              <SelectField
                label="Who"
                value={picked}
                onChange={setPicked}
                options={people.options(type)}
                placeholder="Choose…"
              />
              <button
                type="button"
                className="btn btn-primary"
                disabled={!picked || !canGive || add.isPending}
                onClick={() => add.mutate()}
              >
                Give role
              </button>
            </div>
          ) : null}
          <ErrorNotice error={add.error ?? remove.error} />
        </>
      ) : null}
    </Dialog>
  );
}
