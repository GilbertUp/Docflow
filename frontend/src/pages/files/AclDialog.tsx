import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../../api/client";
import {
  type AclEntry,
  type AclResponse,
  type EffectivePermission,
  RESOURCE_PERMISSIONS,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import {
  Dialog,
  EmptyState,
  ErrorNotice,
  FormActions,
  Notice,
  SelectField,
  Spinner,
} from "../../components/ui";
import { PRINCIPAL_LABELS, type PrincipalType, usePeople } from "../../lib/people";

const PERMISSION_LABEL = Object.fromEntries(RESOURCE_PERMISSIONS);

export function entryKey(entry: AclEntry): string {
  return `${entry.principal_type}:${entry.principal_id}:${entry.permission}`;
}

/** Adds an entry, replacing any existing entry for the same person and permission. */
export function addEntry(entries: AclEntry[], entry: AclEntry): AclEntry[] {
  return [...entries.filter((item) => entryKey(item) !== entryKey(entry)), entry];
}

export function AclDialog({
  anchorId,
  title,
  onClose,
}: {
  anchorId: string;
  title: string;
  onClose: () => void;
}) {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const people = usePeople();
  const acl = useQuery({
    queryKey: ["acl", anchorId],
    queryFn: () => api.get<AclResponse>(`/acl/anchors/${anchorId}`),
  });
  const [entries, setEntries] = useState<AclEntry[]>([]);
  const [saved, setSaved] = useState(false);
  const [type, setType] = useState<PrincipalType>("group");
  const [principal, setPrincipal] = useState("");
  const [permission, setPermission] = useState("read");
  const [effect, setEffect] = useState<"allow" | "deny">("allow");

  useEffect(() => {
    if (acl.data) setEntries(acl.data.entries);
  }, [acl.data]);

  const save = useMutation({
    mutationFn: () => api.put(`/acl/anchors/${anchorId}`, { entries }),
    onSuccess: () => {
      setSaved(true);
      void queryClient.invalidateQueries({ queryKey: ["acl", anchorId] });
      void queryClient.invalidateQueries({ queryKey: ["detail"] });
      void queryClient.invalidateQueries({ queryKey: ["children"] });
      void queryClient.invalidateQueries({ queryKey: ["effective"] });
    },
  });

  const options = people.options(type);
  const dirty = JSON.stringify(entries) !== JSON.stringify(acl.data?.entries ?? []);

  return (
    <Dialog title={`Permissions for ${title}`} onClose={onClose} wide>
      {acl.isPending ? <Spinner /> : null}
      {acl.isError ? <ErrorNotice error={acl.error} /> : null}
      {acl.data ? (
        <>
          <p className="muted">
            Entries here apply to this location and everything inside it. A deny always wins over an
            allow at the same level, and a closer entry overrides one from above.
          </p>

          {entries.length === 0 ? (
            <EmptyState title="No entries set here">
              Access is inherited from above, or nobody has it yet.
            </EmptyState>
          ) : (
            <div className="table-wrap">
              <table>
                <caption className="sr-only">Access entries</caption>
                <thead>
                  <tr>
                    <th scope="col">Who</th>
                    <th scope="col">Permission</th>
                    <th scope="col">Effect</th>
                    <th scope="col">
                      <span className="sr-only">Remove</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((entry) => (
                    <tr key={entryKey(entry)}>
                      <td>
                        {people.label(entry.principal_type, entry.principal_id)}
                        <span className="badge">{PRINCIPAL_LABELS[entry.principal_type]}</span>
                      </td>
                      <td title={PERMISSION_LABEL[entry.permission]}>{entry.permission}</td>
                      <td>
                        <span className={entry.effect === "allow" ? "badge badge-ok" : "badge badge-warn"}>
                          {entry.effect}
                        </span>
                      </td>
                      <td className="actions">
                        <button
                          type="button"
                          className="btn btn-quiet"
                          onClick={() => setEntries(entries.filter((item) => item !== entry))}
                        >
                          Remove<span className="sr-only"> {entry.permission} for this entry</span>
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h3 style={{ marginTop: "1rem" }}>Add an entry</h3>
          {people.enabled ? (
            <div className="acl-grid">
              <SelectField
                label="Type"
                value={type}
                onChange={(value) => {
                  setType(value as PrincipalType);
                  setPrincipal("");
                }}
                options={Object.entries(PRINCIPAL_LABELS).map(([value, label]) => ({ value, label }))}
              />
              <SelectField
                label="Who"
                value={principal}
                onChange={setPrincipal}
                options={options}
                placeholder="Choose…"
              />
              <SelectField
                label="Permission"
                value={permission}
                onChange={setPermission}
                options={RESOURCE_PERMISSIONS.map(([value, text]) => ({
                  value,
                  label: `${value} — ${text}`,
                }))}
              />
              <SelectField
                label="Effect"
                value={effect}
                onChange={(value) => setEffect(value as "allow" | "deny")}
                options={[
                  { value: "allow", label: "Allow" },
                  { value: "deny", label: "Deny" },
                ]}
              />
              <button
                type="button"
                className="btn"
                disabled={!principal}
                onClick={() => {
                  setEntries(
                    addEntry(entries, {
                      principal_type: type,
                      principal_id: principal,
                      permission,
                      effect,
                    }),
                  );
                  setSaved(false);
                }}
              >
                Add
              </button>
            </div>
          ) : (
            <Notice tone="info">
              Choosing people needs access to the user and group lists. You can still remove
              entries.
            </Notice>
          )}

          <ErrorNotice error={save.error} />
          {saved && !dirty ? <Notice tone="success">Saved. The change applies immediately.</Notice> : null}
          <FormActions>
            <button type="button" className="btn" onClick={onClose}>
              Close
            </button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!dirty || save.isPending}
              onClick={() => save.mutate()}
            >
              {save.isPending ? "Saving…" : "Save changes"}
            </button>
          </FormActions>

          {can("users.manage") ? <Inspector anchorId={anchorId} /> : null}
        </>
      ) : null}
    </Dialog>
  );
}

function Inspector({ anchorId }: { anchorId: string }) {
  const people = usePeople();
  const [userId, setUserId] = useState("");
  const result = useQuery({
    queryKey: ["effective", anchorId, userId],
    enabled: userId !== "",
    queryFn: () =>
      api.get<Record<string, EffectivePermission>>(`/users/${userId}/effective-permissions`, {
        query: { anchor_id: anchorId },
      }),
  });

  return (
    <section aria-labelledby="inspector-heading" style={{ marginTop: "1.5rem" }}>
      <h3 id="inspector-heading">Check someone’s access</h3>
      <SelectField
        label="Person"
        value={userId}
        onChange={setUserId}
        options={people.options("user")}
        placeholder="Choose a person…"
      />
      {result.isPending && userId ? <Spinner /> : null}
      <ErrorNotice error={result.error} />
      {result.data ? (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Effective permissions</caption>
            <thead>
              <tr>
                <th scope="col">Permission</th>
                <th scope="col">Result</th>
                <th scope="col">Why</th>
              </tr>
            </thead>
            <tbody>
              {RESOURCE_PERMISSIONS.map(([code]) => {
                const item = result.data[code];
                if (!item) return null;
                const here = item.source_anchor_id === anchorId;
                return (
                  <tr key={code}>
                    <td>{code}</td>
                    <td>
                      <span className={item.allowed ? "badge badge-ok" : "badge badge-warn"}>
                        {item.allowed ? "Allowed" : "Not allowed"}
                      </span>
                    </td>
                    <td className="muted">
                      {item.source_anchor_id
                        ? `${item.reason}${here ? " (set on this item)" : " (inherited from above)"}`
                        : item.reason}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
