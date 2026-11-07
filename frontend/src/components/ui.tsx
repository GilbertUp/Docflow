import {
  type FormEvent,
  type ReactNode,
  type SelectHTMLAttributes,
  useEffect,
  useId,
  useRef,
} from "react";

import { errorMessage } from "../api/client";

export function Notice({
  tone,
  children,
}: {
  tone: "error" | "success" | "info";
  children: ReactNode;
}) {
  // Errors interrupt screen readers, the others wait politely.
  return (
    <p className={`notice notice-${tone}`} role={tone === "error" ? "alert" : "status"}>
      {children}
    </p>
  );
}

export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  return <Notice tone="error">{errorMessage(error)}</Notice>;
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <p className="muted" role="status">
      {label}…
    </p>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children ? <p className="muted">{children}</p> : null}
    </div>
  );
}

interface DialogProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}

/** A modal built on the native dialog element, which handles focus, Escape and inertness. */
export function Dialog({ title, onClose, children, wide = false }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<Element | null>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    // Remember who opened it, once, even when React re-runs effects in development.
    opener.current ??= document.activeElement;
    if (dialog && !dialog.open) {
      dialog.showModal();
      // The browser focuses the first control, which is the close button. People expect the
      // first field, so move there when there is one.
      dialog.querySelector<HTMLElement>("input:not([type=hidden]), select, textarea")?.focus();
    }
    return () => {
      // Removing the element closes it. Calling close() here would fire the close event and
      // ask the parent to remove a dialog that React is only re-mounting.
      const target = opener.current;
      if (target instanceof HTMLElement && !dialog?.isConnected) target.focus();
    };
  }, []);

  return (
    <dialog
      ref={ref}
      className={wide ? "dialog dialog-wide" : "dialog"}
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      <div className="dialog-body">
        <div className="dialog-head">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="btn btn-quiet" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        {children}
      </div>
    </dialog>
  );
}

interface FieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: "text" | "password" | "email" | "number" | "date";
  error?: string | undefined;
  hint?: string;
  required?: boolean;
  autoComplete?: string;
  autoFocus?: boolean;
  inputMode?: "numeric" | "decimal" | "text";
  disabled?: boolean;
  maxLength?: number;
}

export function TextField({
  label,
  value,
  onChange,
  type = "text",
  error,
  hint,
  required,
  autoComplete,
  autoFocus,
  inputMode,
  maxLength,
  disabled,
}: FieldProps) {
  const id = useId();
  const describedBy = [error ? `${id}-error` : null, hint ? `${id}-hint` : null]
    .filter(Boolean)
    .join(" ");
  return (
    <div className="field">
      <label htmlFor={id}>
        {label}
        {required ? <span aria-hidden="true"> *</span> : null}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        required={required}
        autoComplete={autoComplete}
        autoFocus={autoFocus}
        inputMode={inputMode}
        maxLength={maxLength}
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint ? (
        <p id={`${id}-hint`} className="hint">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-error`} className="field-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}

interface SelectFieldProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "onChange"> {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  placeholder?: string;
}

export function SelectField({
  label,
  value,
  onChange,
  options,
  placeholder,
  ...rest
}: SelectFieldProps) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(event) => onChange(event.target.value)} {...rest}>
        {placeholder ? <option value="">{placeholder}</option> : null}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export function Form({
  onSubmit,
  children,
}: {
  onSubmit: () => void;
  children: ReactNode;
}) {
  return (
    <form
      noValidate
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      {children}
    </form>
  );
}

export function FormActions({ children }: { children: ReactNode }) {
  return <div className="form-actions">{children}</div>;
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  danger = false,
  pending,
  error,
  onConfirm,
  onClose,
}: {
  title: string;
  message: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  pending: boolean;
  error: unknown;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Dialog title={title} onClose={onClose}>
      <p>{message}</p>
      <ErrorNotice error={error} />
      <FormActions>
        <button type="button" className="btn" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className={danger ? "btn btn-danger" : "btn btn-primary"}
          disabled={pending}
          onClick={onConfirm}
        >
          {pending ? "Working…" : confirmLabel}
        </button>
      </FormActions>
    </Dialog>
  );
}

/** A compact menu of actions. Native details keeps it keyboard accessible without scripting. */
export function ActionMenu({ label, children }: { label: string; children: ReactNode }) {
  const ref = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    // Clicking anywhere else closes an open menu.
    const closeOnOutsideClick = (event: MouseEvent) => {
      const menu = ref.current;
      if (menu?.open && event.target instanceof Node && !menu.contains(event.target)) {
        menu.removeAttribute("open");
      }
    };
    document.addEventListener("click", closeOnOutsideClick);
    return () => document.removeEventListener("click", closeOnOutsideClick);
  }, []);

  return (
    <details
      ref={ref}
      className="menu"
      onKeyDown={(event) => {
        if (event.key === "Escape" && ref.current?.open) {
          ref.current.removeAttribute("open");
          ref.current.querySelector("summary")?.focus();
        }
      }}
      onClick={(event) => {
        // Close after choosing an action, but not when the menu button itself is clicked.
        if (event.target instanceof Element && event.target.closest(".menu-list button")) {
          setTimeout(() => ref.current?.removeAttribute("open"), 0);
        }
      }}
    >
      <summary className="btn btn-quiet" aria-label={label}>
        Actions ▾
      </summary>
      <div className="menu-list">{children}</div>
    </details>
  );
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

/** A readable name for a browser's technical identifier, such as "Edge on Windows". */
export function describeDevice(userAgent: string | null | undefined): string {
  if (!userAgent) return "Unknown device";
  // Order matters: Edge and Opera also say Chrome, and Android also says Linux.
  const browser = /Edg\//.test(userAgent)
    ? "Edge"
    : /OPR\/|Opera/.test(userAgent)
      ? "Opera"
      : /Firefox\//.test(userAgent)
        ? "Firefox"
        : /Chrome\/|CriOS\//.test(userAgent)
          ? "Chrome"
          : /Safari\//.test(userAgent)
            ? "Safari"
            : null;
  const system = /Windows/.test(userAgent)
    ? "Windows"
    : /Android/.test(userAgent)
      ? "Android"
      : /iPhone|iPad|iPod/.test(userAgent)
        ? "iOS"
        : /Mac OS X|Macintosh/.test(userAgent)
          ? "macOS"
          : /CrOS/.test(userAgent)
            ? "ChromeOS"
            : /Linux/.test(userAgent)
              ? "Linux"
              : null;
  if (browser && system) return `${browser} on ${system}`;
  return browser ?? system ?? "Unknown device";
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
