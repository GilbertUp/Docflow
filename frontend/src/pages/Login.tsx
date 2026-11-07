import { useState } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ErrorNotice, Form, FormActions, Notice, Spinner, TextField } from "../components/ui";

const ORG_KEY = "docuflow.organization";

function rememberedOrganization(): string {
  try {
    return window.localStorage.getItem(ORG_KEY) ?? "";
  } catch {
    return "";
  }
}

function rememberOrganization(value: string): void {
  try {
    window.localStorage.setItem(ORG_KEY, value);
  } catch {
    // Remembering is only a convenience.
  }
}

export function Login() {
  const { status, login, verifyMfa, loadError } = useAuth();
  const location = useLocation();
  const [organization, setOrganization] = useState(rememberedOrganization);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (status === "loading") return <Spinner label="Checking your session" />;
  if (status === "authenticated") {
    const from = (location.state as { from?: string } | null)?.from;
    return <Navigate to={from ?? "/files"} replace />;
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(caught);
      if (caught instanceof ApiError && caught.code === "unauthenticated") setCode("");
    } finally {
      setBusy(false);
    }
  }

  if (status === "mfa") {
    return (
      <div className="centered">
        <div className="panel auth-card">
          <h1>Check your code</h1>
          <p className="muted">
            Enter the 6-digit code from your authenticator app, or one of your recovery codes.
          </p>
          <Form
            onSubmit={() =>
              void run(async () => {
                await verifyMfa(code.trim());
              })
            }
          >
            <TextField
              label="Code"
              value={code}
              onChange={setCode}
              autoComplete="one-time-code"
              inputMode="text"
              autoFocus
              required
            />
            <ErrorNotice error={error} />
            <FormActions>
              <button type="submit" className="btn btn-primary" disabled={busy || !code.trim()}>
                {busy ? "Checking…" : "Verify"}
              </button>
            </FormActions>
          </Form>
        </div>
      </div>
    );
  }

  return (
    <div className="centered">
      <div className="panel auth-card">
        <h1>Sign in to DocuFlow</h1>
        {loadError ? <Notice tone="error">{loadError}</Notice> : null}
        <Form
          onSubmit={() =>
            void run(async () => {
              rememberOrganization(organization.trim());
              await login(organization.trim(), email.trim(), password);
            })
          }
        >
          <TextField
            label="Organization"
            value={organization}
            onChange={setOrganization}
            autoComplete="organization"
            hint="The short name your administrator gave you."
            required
          />
          <TextField
            label="Email"
            type="email"
            value={email}
            onChange={setEmail}
            autoComplete="username"
            required
          />
          <TextField
            label="Password"
            type="password"
            value={password}
            onChange={setPassword}
            autoComplete="current-password"
            required
          />
          <ErrorNotice error={error} />
          <FormActions>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={busy || !organization.trim() || !email.trim() || !password}
            >
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </FormActions>
        </Form>
      </div>
    </div>
  );
}
