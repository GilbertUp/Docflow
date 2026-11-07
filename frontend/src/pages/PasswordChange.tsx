import { useState } from "react";

import { api, fieldErrors } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ErrorNotice, Form, FormActions, Notice, TextField } from "../components/ui";

export const PASSWORD_HINT = "At least 12 characters. Avoid common passwords and your own email name.";

export function PasswordForm({ onDone }: { onDone: () => void | Promise<void> }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const mismatch = confirm.length > 0 && next !== confirm;

  async function submit() {
    if (next !== confirm) return;
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/password", { current_password: current, new_password: next });
      setCurrent("");
      setNext("");
      setConfirm("");
      await onDone();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Form onSubmit={() => void submit()}>
      <TextField
        label="Current password"
        type="password"
        value={current}
        onChange={setCurrent}
        autoComplete="current-password"
        required
      />
      <TextField
        label="New password"
        type="password"
        value={next}
        onChange={setNext}
        autoComplete="new-password"
        hint={PASSWORD_HINT}
        error={fieldErrors(error)["new_password"]}
        required
      />
      <TextField
        label="Confirm new password"
        type="password"
        value={confirm}
        onChange={setConfirm}
        autoComplete="new-password"
        error={mismatch ? "The passwords do not match." : undefined}
        required
      />
      <ErrorNotice error={error} />
      <FormActions>
        <button
          type="submit"
          className="btn btn-primary"
          disabled={busy || !current || !next || next !== confirm}
        >
          {busy ? "Saving…" : "Change password"}
        </button>
      </FormActions>
    </Form>
  );
}

/** Shown instead of the app until a new user replaces the password an administrator set. */
export function ForcedPasswordChange() {
  const { refresh, logout } = useAuth();
  return (
    <div className="centered">
      <div className="panel auth-card">
        <h1>Choose a new password</h1>
        <Notice tone="info">
          An administrator set your password. Choose your own before you continue.
        </Notice>
        <PasswordForm onDone={refresh} />
        <p>
          <button type="button" className="btn btn-quiet" onClick={() => void logout()}>
            Sign out instead
          </button>
        </p>
      </div>
    </div>
  );
}
