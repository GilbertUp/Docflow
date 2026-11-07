import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { Group, Page, User } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import {
  Dialog,
  EmptyState,
  ErrorNotice,
  Form,
  FormActions,
  SelectField,
  Spinner,
  TextField,
} from "../../components/ui";
import { usePeople } from "../../lib/people";

export function Groups() {
  const { can } = useAuth();
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<Group | null>(null);
  const groups = useQuery({
    queryKey: ["admin", "groups"],
    queryFn: () => api.get<Page<Group>>("/groups", { query: { limit: 200 } }),
  });

  return (
    <div>
      <h1>Groups</h1>
      <div className="toolbar">
        {can("groups.manage") ? (
          <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
            New group
          </button>
        ) : null}
      </div>
      <div className="split">
        <div className="panel">
          <h2>All groups</h2>
          {groups.isPending ? (
            <Spinner />
          ) : groups.isError ? (
            <ErrorNotice error={groups.error} />
          ) : groups.data.items.length === 0 ? (
            <EmptyState title="No groups yet">Groups make it easy to give a team access at once.</EmptyState>
          ) : (
            <ul className="tree">
              {groups.data.items.map((group) => (
                <li key={group.id}>
                  <button
                    type="button"
                    className="tree-link"
                    aria-pressed={selected?.id === group.id}
                    onClick={() => setSelected(group)}
                  >
                    {group.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        {selected ? (
          <Members key={selected.id} group={selected} />
        ) : (
          <div className="panel">
            <EmptyState title="Choose a group">See and change who is in it.</EmptyState>
          </div>
        )}
      </div>
      {creating ? <NewGroupDialog onClose={() => setCreating(false)} /> : null}
    </div>
  );
}

function Members({ group }: { group: Group }) {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const people = usePeople();
  const [userId, setUserId] = useState("");
  const manage = can("groups.manage");
  const members = useQuery({
    queryKey: ["admin", "members", group.id],
    queryFn: () => api.get<User[]>(`/groups/${group.id}/members`),
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["admin", "members", group.id] });
  const add = useMutation({
    mutationFn: () => api.put(`/groups/${group.id}/members/${userId}`),
    onSuccess: () => {
      setUserId("");
      refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/groups/${group.id}/members/${id}`),
    onSuccess: refresh,
  });
  const memberIds = new Set((members.data ?? []).map((member) => member.id));

  return (
    <div className="panel">
      <h2>{group.name}</h2>
      {group.description ? <p className="muted">{group.description}</p> : null}
      {manage ? (
        <div className="toolbar" style={{ alignItems: "end" }}>
          <div className="grow">
            <SelectField
              label="Add a person"
              value={userId}
              onChange={setUserId}
              options={people.options("user").filter((option) => !memberIds.has(option.value))}
              placeholder="Choose…"
            />
          </div>
          <button
            type="button"
            className="btn btn-primary"
            style={{ marginBottom: "0.9rem" }}
            disabled={!userId || add.isPending}
            onClick={() => add.mutate()}
          >
            Add
          </button>
        </div>
      ) : null}
      <ErrorNotice error={add.error ?? remove.error} />
      {members.isPending ? (
        <Spinner />
      ) : members.isError ? (
        <ErrorNotice error={members.error} />
      ) : members.data.length === 0 ? (
        <EmptyState title="No members yet" />
      ) : (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Members of {group.name}</caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Email</th>
                <th scope="col">
                  <span className="sr-only">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {members.data.map((member) => (
                <tr key={member.id}>
                  <td>{member.display_name}</td>
                  <td>{member.email}</td>
                  <td className="actions">
                    {manage ? (
                      <button
                        type="button"
                        className="btn btn-quiet"
                        disabled={remove.isPending}
                        onClick={() => remove.mutate(member.id)}
                      >
                        Remove<span className="sr-only"> {member.display_name}</span>
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function NewGroupDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const save = useMutation({
    mutationFn: () => api.post("/groups", { name: name.trim(), description: description.trim() }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "groups"] });
      void queryClient.invalidateQueries({ queryKey: ["people"] });
      onClose();
    },
  });
  return (
    <Dialog title="New group" onClose={onClose}>
      <Form onSubmit={() => save.mutate()}>
        <TextField label="Group name" value={name} onChange={setName} autoFocus required />
        <TextField label="Description" value={description} onChange={setDescription} />
        <ErrorNotice error={save.error} />
        <FormActions>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={save.isPending || !name.trim()}>
            {save.isPending ? "Creating…" : "Create group"}
          </button>
        </FormActions>
      </Form>
    </Dialog>
  );
}
