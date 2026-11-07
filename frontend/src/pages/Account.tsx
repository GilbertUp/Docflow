import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { useState } from "react";

import { api } from "../api/client";
import type { MfaSetup, Session } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import {
  ErrorNotice,
  Form,
  FormActions,
  Notice,
  Spinner,
  TextField,
  describeDevice,
  formatDate,
} from "../components/ui";
import { PasswordForm } from "./PasswordChange";

export function Account() {
  const { me } = useAuth();
  const [changed, setChanged] = useState(false);
  if (!me) return null;

  return (
    <div>
      <h1>Your account</h1>
      <div className="panel">
        <h2>Profile</h2>
        <dl className="stats">
          <div>
            <dt>Name</dt>
            <dd>{me.user.display_name}</dd>
          </div>
          <div>
            <dt>Email</dt>
            <dd>{me.user.email}</dd>
          </div>
        </dl>
      </div>

      <div className="panel">
        <h2>Password</h2>
        {changed ? <Notice tone="success">Password changed. Your other sessions were signed out.</Notice> : null}
        <PasswordForm onDone={() => setChanged(true)} />
      </div>

      <div className="panel">
        <h2>Two-step sign-in</h2>
        {me.user.mfa_enabled ? (
          <Notice tone="success">
            Two-step sign-in is on. You will be asked for a code each time you sign in.
          </Notice>
        ) : (
          <MfaSetupFlow />
        )}
      </div>

      <SessionsPanel />
    </div>
  );
}

function MfaSetupFlow() {
  const { refresh } = useAuth();
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState<string[] | null>(null);
  const start = useMutation({ mutationFn: () => api.post<MfaSetup>("/auth/mfa/setup"), onSuccess: setSetup });
  const confirm = useMutation({
    mutationFn: () => api.post<{ recovery_codes: string[] }>("/auth/mfa/confirm", { code: code.trim() }),
    onSuccess: (result) => setRecovery(result.recovery_codes),
  });

  if (recovery) {
    return (
      <div>
        <Notice tone="success">Two-step sign-in is on.</Notice>
        <h3>Save your recovery codes</h3>
        <p className="muted">
          Each code works once if you lose your phone. They will not be shown again, so keep them
          somewhere safe.
        </p>
        <ul className="token-box" aria-label="Recovery codes">
          {recovery.map((item) => (
            <li key={item} style={{ listStyle: "none" }}>
              {item}
            </li>
          ))}
        </ul>
        <FormActions>
          <button type="button" className="btn btn-primary" onClick={() => void refresh()}>
            I have saved them
          </button>
        </FormActions>
      </div>
    );
  }

  if (!setup) {
    return (
      <div>
        <p className="muted">
          Add a second step with an authenticator app such as the one on your phone. It protects your
          account even if your password leaks.
        </p>
        <ErrorNotice error={start.error} />
        <button type="button" className="btn btn-primary" disabled={start.isPending} onClick={() => start.mutate()}>
          {start.isPending ? "Preparing…" : "Set up two-step sign-in"}
        </button>
      </div>
    );
  }

  return (
    <Form onSubmit={() => confirm.mutate()}>
      <p>Scan this code with your authenticator app, then enter the 6-digit code it shows.</p>
      <div style={{ background: "#fff", padding: "0.75rem", display: "inline-block", borderRadius: 8 }}>
        <QRCodeSVG value={setup.uri} size={168} title="Authenticator setup code" />
      </div>
      <p className="muted">
        Cannot scan it? Enter this key by hand: <code>{setup.secret}</code>
      </p>
      <TextField
        label="6-digit code"
        value={code}
        onChange={setCode}
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={6}
        required
      />
      <ErrorNotice error={confirm.error} />
      <FormActions>
        <button type="submit" className="btn btn-primary" disabled={confirm.isPending || code.trim().length < 6}>
          {confirm.isPending ? "Checking…" : "Turn on"}
        </button>
      </FormActions>
    </Form>
  );
}

function SessionsPanel() {
  const queryClient = useQueryClient();
  const sessions = useQuery({
    queryKey: ["account", "sessions"],
    queryFn: () => api.get<Session[]>("/auth/sessions"),
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api.delete(`/auth/sessions/${id}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["account", "sessions"] }),
  });

  return (
    <div className="panel">
      <h2>Where you are signed in</h2>
      {sessions.isPending ? <Spinner /> : null}
      <ErrorNotice error={sessions.error ?? revoke.error} />
      {sessions.data ? (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Active sessions</caption>
            <thead>
              <tr>
                <th scope="col">Device</th>
                <th scope="col" className="hide-narrow">
                  Address
                </th>
                <th scope="col">Started</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {sessions.data.map((item) => (
                <tr key={item.id}>
                  <td>
                    {describeDevice(item.device)}
                    {item.current ? <span className="badge badge-ok">This device</span> : null}
                  </td>
                  <td className="hide-narrow">{item.ip ?? "—"}</td>
                  <td>{formatDate(item.created_at)}</td>
                  <td className="actions">
                    {item.current ? null : (
                      <button
                        type="button"
                        className="btn btn-quiet"
                        disabled={revoke.isPending}
                        onClick={() => revoke.mutate(item.id)}
                      >
                        Sign out<span className="sr-only"> {describeDevice(item.device)}</span>
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
