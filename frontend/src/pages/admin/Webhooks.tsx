import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type {
  WebhookCreatedRow,
  WebhookDeliveryRow,
  WebhookEventType,
  WebhookRow,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import {
  EmptyState,
  ErrorNotice,
  Form,
  FormActions,
  Notice,
  Spinner,
  TextField,
  formatDate,
} from "../../components/ui";

/** The events chosen, kept in the order the server lists them. */
export function toggleEvent(all: string[], chosen: string[], event: string, on: boolean): string[] {
  const next = new Set(chosen);
  if (on) next.add(event);
  else next.delete(event);
  return all.filter((item) => next.has(item));
}

const DELIVERY_STATUS: Record<string, string> = {
  pending: "Waiting",
  succeeded: "Delivered",
  failed: "Gave up",
};

function Deliveries({ hook }: { hook: WebhookRow }) {
  const queryClient = useQueryClient();
  const list = useQuery({
    queryKey: ["webhook-deliveries", hook.id],
    queryFn: () => api.get<WebhookDeliveryRow[]>(`/webhooks/${hook.id}/deliveries`),
  });
  const again = useMutation({
    mutationFn: (id: string) => api.post(`/webhooks/${hook.id}/deliveries/${id}/redeliver`, {}),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["webhook-deliveries", hook.id] }),
  });
  return (
    <details>
      <summary>Recent deliveries</summary>
      <ErrorNotice error={list.error ?? again.error} />
      {list.data && list.data.length === 0 ? <p className="muted">Nothing sent yet.</p> : null}
      <ul className="plain-list">
        {(list.data ?? []).map((item) => (
          <li key={item.id}>
            {item.event_type}: {DELIVERY_STATUS[item.status] ?? item.status}
            {item.attempts > 0 ? `, ${item.attempts} ${item.attempts === 1 ? "try" : "tries"}` : ""},{" "}
            {formatDate(item.created_at)}
            {item.error ? <div className="muted">{item.error}</div> : null}
            {item.status === "failed" ? (
              <button
                type="button"
                className="btn btn-quiet"
                disabled={again.isPending}
                onClick={() => again.mutate(item.id)}
              >
                Send again<span className="sr-only"> {item.event_type}</span>
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </details>
  );
}

export function Webhooks() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [secret, setSecret] = useState<{ name: string; value: string } | null>(null);
  const hooks = useQuery({
    queryKey: ["webhooks"],
    enabled: can("integrations.manage"),
    queryFn: () => api.get<WebhookRow[]>("/webhooks"),
  });
  const events = useQuery({
    queryKey: ["webhook-events"],
    queryFn: () => api.get<WebhookEventType[]>("/webhook-events"),
  });
  const reload = () => void queryClient.invalidateQueries({ queryKey: ["webhooks"] });
  const create = useMutation({
    mutationFn: () => api.post<WebhookCreatedRow>("/webhooks", { name: name.trim(), url: url.trim(), events: chosen }),
    onSuccess: (made) => {
      setSecret({ name: made.name, value: made.secret });
      setName("");
      setUrl("");
      setChosen([]);
      reload();
    },
  });
  const rotate = useMutation({
    mutationFn: (hook: WebhookRow) => api.post<WebhookCreatedRow>(`/webhooks/${hook.id}/secret`, {}),
    onSuccess: (made) => setSecret({ name: made.name, value: made.secret }),
  });
  const toggle = useMutation({
    mutationFn: (hook: WebhookRow) =>
      api.patch(`/webhooks/${hook.id}`, { status: hook.status === "active" ? "disabled" : "active" }),
    onSuccess: reload,
  });
  const ping = useMutation({
    mutationFn: (hook: WebhookRow) => api.post(`/webhooks/${hook.id}/test`, {}),
    onSuccess: (_data, hook) =>
      void queryClient.invalidateQueries({ queryKey: ["webhook-deliveries", hook.id] }),
  });
  const remove = useMutation({
    mutationFn: (hook: WebhookRow) => api.delete(`/webhooks/${hook.id}`),
    onSuccess: reload,
  });

  if (!can("integrations.manage")) {
    return (
      <div>
        <h1>Webhooks</h1>
        <Notice tone="info">You do not have permission to manage webhooks.</Notice>
      </div>
    );
  }
  const all = (events.data ?? []).map((e) => e.type);

  return (
    <div>
      <h1>Webhooks</h1>
      <p className="muted">
        A webhook tells another system when something happens here. It says what happened and which
        item it was about, never what was in it. The other system asks through the API, with its own
        key, for anything more.
      </p>
      {secret ? (
        <Notice tone="success">
          Signing secret for {secret.name}: <code>{secret.value}</code>. Copy it now. It cannot be
          shown again, only replaced.
        </Notice>
      ) : null}
      <ErrorNotice error={hooks.error ?? toggle.error ?? ping.error ?? remove.error ?? rotate.error} />
      {hooks.isPending ? <Spinner label="Loading webhooks" /> : null}
      {hooks.data && hooks.data.length === 0 ? (
        <EmptyState title="No webhooks">Add one below to start sending events.</EmptyState>
      ) : null}
      <ul className="plain-list">
        {(hooks.data ?? []).map((hook) => (
          <li key={hook.id}>
            <strong>{hook.name}</strong>
            {hook.status === "disabled" ? <span className="badge badge-warn">Switched off</span> : null}
            <div className="muted">
              {hook.url}. {hook.events.length} {hook.events.length === 1 ? "event" : "events"}
            </div>
            <div className="toolbar">
              <button type="button" className="btn btn-quiet" disabled={ping.isPending} onClick={() => ping.mutate(hook)}>
                Send a test<span className="sr-only"> to {hook.name}</span>
              </button>
              <button type="button" className="btn btn-quiet" onClick={() => toggle.mutate(hook)}>
                {hook.status === "active" ? "Switch off" : "Switch on"}
                <span className="sr-only"> {hook.name}</span>
              </button>
              <button type="button" className="btn btn-quiet" onClick={() => rotate.mutate(hook)}>
                New secret<span className="sr-only"> for {hook.name}</span>
              </button>
              <button type="button" className="btn btn-quiet" onClick={() => remove.mutate(hook)}>
                Delete<span className="sr-only"> {hook.name}</span>
              </button>
            </div>
            <Deliveries hook={hook} />
          </li>
        ))}
      </ul>

      <h2>Add a webhook</h2>
      <Form onSubmit={() => create.mutate()}>
        <TextField label="Name" value={name} onChange={setName} maxLength={120} />
        <TextField
          label="Address to call"
          value={url}
          onChange={setUrl}
          hint="Must start with https:// and be reachable from the internet. Internal addresses are refused."
        />
        <fieldset>
          <legend>Tell it when</legend>
          {(events.data ?? []).map((event) => (
            <label className="check" key={event.type}>
              <input
                type="checkbox"
                checked={chosen.includes(event.type)}
                onChange={(e) => setChosen(toggleEvent(all, chosen, event.type, e.target.checked))}
              />
              {event.description}
            </label>
          ))}
        </fieldset>
        <ErrorNotice error={create.error} />
        <FormActions>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={create.isPending || !name.trim() || !url.trim() || chosen.length === 0}
          >
            Add webhook
          </button>
        </FormActions>
      </Form>
    </div>
  );
}
