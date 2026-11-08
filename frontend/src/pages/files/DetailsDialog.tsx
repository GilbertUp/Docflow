import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api, fieldErrors } from "../../api/client";
import type {
  DocumentTypeRow,
  IndexField,
  IndexResult,
  SuggestedField,
  Suggestions,
} from "../../api/types";
import {
  Dialog,
  ErrorNotice,
  Form,
  FormActions,
  Notice,
  SelectField,
  Spinner,
  TextField,
} from "../../components/ui";
import { useRefreshFiles } from "./dialogs";

type Values = Record<string, string>;

function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}

function valuesOf(fields: IndexField[]): Values {
  return Object.fromEntries(fields.map((field) => [field.key, asText(field.value)]));
}

/** One input, of the kind the field's type calls for. */
export function FieldInput({
  field,
  value,
  error,
  disabled,
  onChange,
}: {
  field: Pick<IndexField, "key" | "label" | "data_type" | "required" | "read_only" | "choices">;
  value: string;
  error: string | undefined;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const locked = disabled || field.read_only;
  const label = field.read_only ? `${field.label} (filled in by the system)` : field.label;
  if (field.data_type === "boolean" || field.data_type === "choice") {
    const options =
      field.data_type === "boolean"
        ? [
            { value: "yes", label: "Yes" },
            { value: "no", label: "No" },
          ]
        : (field.choices ?? []).map((choice) => ({ value: choice.value, label: choice.label }));
    return (
      <div>
        <SelectField
          label={label}
          value={value}
          onChange={onChange}
          options={options}
          placeholder="Not set"
          disabled={locked}
          required={field.required}
          aria-invalid={error ? true : undefined}
        />
        {error ? (
          <p className="field-error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
    );
  }
  return (
    <TextField
      label={label}
      value={value}
      onChange={onChange}
      type={field.data_type === "date" ? "date" : "text"}
      inputMode={field.data_type === "number" ? "decimal" : "text"}
      error={error}
      disabled={locked}
      required={field.required}
    />
  );
}

const BAND_TEXT: Record<string, string> = {
  auto: "high confidence",
  review: "needs a check",
  weak: "weak guess",
};

/** What was found for one field, where, and a way to take it. */
function SuggestionHint({
  suggestion,
  current,
  canEdit,
  onUse,
}: {
  suggestion: SuggestedField;
  current: string;
  canEdit: boolean;
  onUse: (value: string) => void;
}) {
  const value = suggestion.value ?? suggestion.raw_value;
  if (suggestion.state === "rejected" || (current !== "" && current === value)) return null;
  const where = suggestion.page_number ? `, page ${suggestion.page_number}` : "";
  return (
    <p className="muted">
      Suggested for {suggestion.label}: <strong>{value}</strong> (
      {BAND_TEXT[suggestion.band] ?? suggestion.band}
      {where}
      {suggestion.validation_state === "invalid" ? ", does not fit the field's rules" : ""}){" "}
      {canEdit && suggestion.validation_state === "valid" ? (
        <button
          type="button"
          className="btn btn-quiet"
          onClick={() => onUse(value)}
          aria-label={`Use the suggested ${suggestion.label}`}
        >
          Use
        </button>
      ) : null}
    </p>
  );
}

/** A document's type and the fields that type asks for. */
export function DetailsDialog({
  documentId,
  title,
  canEdit,
  onClose,
}: {
  documentId: string;
  title: string;
  canEdit: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const refresh = useRefreshFiles();
  const [typeId, setTypeId] = useState<string | null>(null);
  const [values, setValues] = useState<Values>({});
  const [saved, setSaved] = useState<IndexResult | null>(null);

  const index = useQuery({
    queryKey: ["index", documentId],
    queryFn: () => api.get<IndexResult>(`/documents/${documentId}/index`),
  });
  const types = useQuery({
    queryKey: ["document-types"],
    queryFn: () => api.get<DocumentTypeRow[]>("/document-types"),
  });

  const suggestions = useQuery({
    queryKey: ["suggestions", documentId],
    queryFn: () => api.get<Suggestions>(`/documents/${documentId}/suggestions`),
    retry: false,
  });
  const findAgain = useMutation({
    mutationFn: () => api.post(`/documents/${documentId}/suggestions/run`, {}),
    onSuccess: () => {
      // The job runs in the background, so look again shortly.
      window.setTimeout(() => {
        void queryClient.invalidateQueries({ queryKey: ["suggestions", documentId] });
        void queryClient.invalidateQueries({ queryKey: ["index", documentId] });
      }, 3000);
    },
  });
  const proposed = suggestions.data?.classification;
  const byKey = useMemo(
    () => new Map((suggestions.data?.fields ?? []).map((item) => [item.key, item])),
    [suggestions.data],
  );

  // Start the form from what the document holds.
  useEffect(() => {
    if (index.data) {
      setTypeId(index.data.document_type_id);
      setValues(valuesOf(index.data.fields));
    }
  }, [index.data]);

  const save = useMutation({
    mutationFn: () =>
      api.put<IndexResult>(`/documents/${documentId}/index`, {
        document_type_id: typeId,
        values: typeId ? values : {},
        row_version: index.data?.row_version ?? null,
      }),
    onSuccess: (result) => {
      setSaved(result);
      queryClient.setQueryData(["index", documentId], result);
      refresh();
    },
  });

  // The fields to show: the document's own when its type is unchanged, else the chosen type's.
  const unchanged = typeId !== null && typeId === index.data?.document_type_id;
  const chosen = types.data?.find((item) => item.id === typeId);
  const shown: Pick<
    IndexField,
    "key" | "label" | "data_type" | "required" | "read_only" | "choices"
  >[] = useMemo(() => {
    if (!typeId) return [];
    if (unchanged && index.data) return index.data.fields;
    return chosen?.fields ?? [];
  }, [typeId, unchanged, index.data, chosen]);
  const errors = fieldErrors(save.error);

  const options = useMemo(() => {
    const list = (types.data ?? []).map((item) => ({ value: item.id, label: item.name }));
    // Keep an archived type visible on the document that still has it.
    if (index.data?.document_type_id && !list.some((o) => o.value === index.data?.document_type_id)) {
      list.push({
        value: index.data.document_type_id,
        label: `${index.data.type_name ?? "Unknown"} (archived)`,
      });
    }
    return list;
  }, [types.data, index.data]);

  function changeType(next: string) {
    setSaved(null);
    save.reset();
    setTypeId(next || null);
    // Carry over what was typed into fields the new type also has.
    setValues((current) => {
      const next_ = types.data?.find((item) => item.id === next);
      return Object.fromEntries(
        (next_?.fields ?? []).map((field) => [
          field.key,
          current[field.key] ?? (field.default_value ?? ""),
        ]),
      );
    });
  }

  const missing = (saved ?? index.data)?.missing_required ?? [];

  return (
    <Dialog title={`Details of ${title}`} onClose={onClose} wide>
      {index.isPending || types.isPending ? <Spinner label="Loading the details" /> : null}
      <ErrorNotice error={index.error ?? types.error} />
      {index.data ? (
        <Form onSubmit={() => save.mutate()}>
          {types.data && types.data.length === 0 ? (
            <Notice tone="info">
              No document types exist yet. Someone who manages document types can add them.
            </Notice>
          ) : null}
          <SelectField
            label="Document type"
            value={typeId ?? ""}
            onChange={changeType}
            options={options}
            placeholder="No type"
            disabled={!canEdit}
          />
          {proposed && proposed.type_id && !proposed.applied && proposed.type_id !== typeId ? (
            <p className="muted">
              This looks like a {proposed.type_name} ({BAND_TEXT[proposed.band] ?? proposed.band}).{" "}
              {canEdit ? (
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => changeType(proposed.type_id ?? "")}
                >
                  Use {proposed.type_name}
                </button>
              ) : null}
            </p>
          ) : null}
          {typeId && index.data.outdated && unchanged ? (
            <Notice tone="info">
              This document was filled in with an older version of its fields. Saving moves it to
              the current version.
            </Notice>
          ) : null}
          {shown.map((field) => {
            const suggestion = byKey.get(field.key);
            const setValue = (value: string) =>
              setValues((current) => ({ ...current, [field.key]: value }));
            return (
              <div key={`${typeId}-${field.key}`}>
                <FieldInput
                  field={field}
                  value={values[field.key] ?? ""}
                  error={errors[field.key]}
                  disabled={!canEdit}
                  onChange={setValue}
                />
                {suggestion && !field.read_only ? (
                  <SuggestionHint
                    suggestion={suggestion}
                    current={values[field.key] ?? ""}
                    canEdit={canEdit}
                    onUse={setValue}
                  />
                ) : null}
              </div>
            );
          })}
          {canEdit && suggestions.data?.text_state === "done" ? (
            <div className="toolbar">
              <button
                type="button"
                className="btn"
                disabled={findAgain.isPending}
                onClick={() => findAgain.mutate()}
              >
                Look for values again
              </button>
            </div>
          ) : null}
          <ErrorNotice error={findAgain.error} />
          <ErrorNotice error={save.error && Object.keys(errors).length === 0 ? save.error : null} />
          {Object.keys(errors).length > 0 ? (
            <Notice tone="error">Some values are not valid. They are marked above.</Notice>
          ) : null}
          {saved ? (
            <Notice tone={saved.complete || !saved.document_type_id ? "success" : "info"}>
              Saved.
              {saved.document_type_id && !saved.complete
                ? ` Still needed: ${saved.missing_required
                    .map((key) => saved.fields.find((field) => field.key === key)?.label ?? key)
                    .join(", ")}.`
                : ""}
            </Notice>
          ) : typeId && missing.length > 0 && unchanged ? (
            <p className="muted">
              Still needed:{" "}
              {missing
                .map((key) => index.data?.fields.find((field) => field.key === key)?.label ?? key)
                .join(", ")}
              .
            </p>
          ) : null}
          <FormActions>
            <button type="button" className="btn" onClick={onClose}>
              {canEdit ? "Cancel" : "Done"}
            </button>
            {canEdit ? (
              <button type="submit" className="btn btn-primary" disabled={save.isPending}>
                {save.isPending ? "Saving…" : "Save"}
              </button>
            ) : null}
          </FormActions>
        </Form>
      ) : null}
    </Dialog>
  );
}
