import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { ApiKey, ApiKeyCreated, Page } from "../../api/types";
import {
  ConfirmDialog,
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

export function ApiKeys() {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<ApiKey | null>(null);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const keys = useQuery({
    queryKey: ["admin", "api-keys"],
    queryFn: () => api.get<Page<ApiKey>>("/api-keys", { query: { limit: 100 } }),
  });
  const revoke = useMutation({
    mutationFn: (key: ApiKey) => api.delete(`/api-keys/${key.id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "api-keys"] });
      setRevoking(null);
    },
  });

  return (
    <div>
      <h1>API keys</h1>
      <p className="muted">
        Keys let other systems call DocuFlow. A new key has no access until you give its service
        account a role or permissions.
      </p>
      <div className="toolbar">
        <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
          New key
        </button>
      </div>
      <div className="panel">
        {keys.isPending ? (
          <Spinner />
        ) : keys.isError ? (
          <ErrorNotice error={keys.error} />
        ) : keys.data.items.length === 0 ? (
          <EmptyState title="No API keys" />
        ) : (
          <div className="table-wrap">
            <table>
              <caption className="sr-only">API keys</caption>
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Starts with</th>
                  <th scope="col" className="hide-narrow">
                    Last used
                  </th>
                  <th scope="col">Expires</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {keys.data.items.map((key) => (
                  <tr key={key.id}>
                    <td>
                      {key.name}
                      {key.revoked_at ? <span className="badge badge-warn">Revoked</span> : null}
                    </td>
                    <td>
                      <code>{key.prefix}…</code>
                    </td>
                    <td className="hide-narrow">{formatDate(key.last_used_at)}</td>
                    <td>{key.expires_at ? formatDate(key.expires_at) : "Never"}</td>
                    <td className="actions">
                      {key.revoked_at ? null : (
                        <button type="button" className="btn btn-quiet" onClick={() => setRevoking(key)}>
                          Revoke<span className="sr-only"> {key.name}</span>
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {creating ? (
        <NewKeyDialog
          onClose={() => setCreating(false)}
          onCreated={(key) => {
            setCreating(false);
            setCreated(key);
            void queryClient.invalidateQueries({ queryKey: ["admin", "api-keys"] });
          }}
        />
      ) : null}
      {created ? <TokenDialog created={created} onClose={() => setCreated(null)} /> : null}
      {revoking ? (
        <ConfirmDialog
          title="Revoke key"
          message={
            <>
              Revoke <strong>{revoking.name}</strong>? Anything using it stops working immediately.
            </>
          }
          confirmLabel="Revoke"
          danger
          pending={revoke.isPending}
          error={revoke.error}
          onConfirm={() => revoke.mutate(revoking)}
          onClose={() => setRevoking(null)}
        />
      ) : null}
    </div>
  );
}

function NewKeyDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (key: ApiKeyCreated) => void;
}) {
  const [name, setName] = useState("");
  const [days, setDays] = useState("90");
  const save = useMutation({
    mutationFn: () =>
      api.post<ApiKeyCreated>("/api-keys", {
        name: name.trim(),
        expires_days: days.trim() ? Number(days) : null,
      }),
    onSuccess: onCreated,
  });
  return (
    <Dialog title="New API key" onClose={onClose}>
      <Form onSubmit={() => save.mutate()}>
        <TextField label="Name" value={name} onChange={setName} autoFocus required hint="For example, the system that will use it." />
        <TextField
          label="Expires after (days)"
          type="number"
          value={days}
          onChange={setDays}
          hint="Leave empty for a key that never expires."
        />
        <ErrorNotice error={save.error} />
        <FormActions>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={save.isPending || !name.trim()}>
            {save.isPending ? "Creating…" : "Create key"}
          </button>
        </FormActions>
      </Form>
    </Dialog>
  );
}

function TokenDialog({ created, onClose }: { created: ApiKeyCreated; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <Dialog title="Copy your new key" onClose={onClose}>
      <Notice tone="info">
        This is the only time the key is shown. Store it somewhere safe now. If you lose it, revoke
        it and make a new one.
      </Notice>
      <p className="token-box" data-testid="api-token">
        {created.token}
      </p>
      <p className="muted">
        Its service account has no access yet. Give it a role or permissions before using it.
      </p>
      <FormActions>
        <button
          type="button"
          className="btn"
          onClick={() => {
            void navigator.clipboard?.writeText(created.token).then(() => setCopied(true));
          }}
        >
          {copied ? "Copied" : "Copy"}
        </button>
        <button type="button" className="btn btn-primary" onClick={onClose}>
          I have stored it
        </button>
      </FormActions>
    </Dialog>
  );
}
