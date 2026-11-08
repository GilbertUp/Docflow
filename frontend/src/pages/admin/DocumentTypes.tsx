import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, fieldErrors } from "../../api/client";
import type { DocumentTypeRow, FieldDefinition } from "../../api/types";
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

const KINDS = [
  { value: "text", label: "Text" },
  { value: "number", label: "Number" },
  { value: "date", label: "Date" },
  { value: "boolean", label: "Yes or no" },
  { value: "choice", label: "One of a list" },
];

/** What a person types in the editor, before it becomes a field definition. */
interface Draft {
  id: number;
  key: string;
  keyEdited: boolean;
  label: string;
  data_type: FieldDefinition["data_type"];
  required: boolean;
  default_value: string;
  pattern: string;
  min_length: string;
  max_length: string;
  min: string;
  max: string;
  choices: string;
  labels: string;
  extract_pattern: string;
}

let nextId = 1;

function blank(): Draft {
  return {
    id: nextId++,
    key: "",
    keyEdited: false,
    label: "",
    data_type: "text",
    required: false,
    default_value: "",
    pattern: "",
    min_length: "",
    max_length: "",
    min: "",
    max: "",
    choices: "",
    labels: "",
    extract_pattern: "",
  };
}

/** A key from a label: lowercase words joined by underscores. */
export function keyFromLabel(label: string): string {
  const words = label
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return /^[a-z]/.test(words) ? words.slice(0, 40) : words ? `f_${words}`.slice(0, 40) : "";
}

function toDraft(field: FieldDefinition): Draft {
  const rules = field.validation ?? {};
  const text = (name: string) => (rules[name] === undefined ? "" : String(rules[name]));
  return {
    id: nextId++,
    key: field.key,
    keyEdited: true,
    label: field.label,
    data_type: field.data_type,
    required: field.required ?? false,
    default_value: field.default_value ?? "",
    pattern: text("pattern"),
    min_length: text("min_length"),
    max_length: text("max_length"),
    min: text("min"),
    max: text("max"),
    labels: Array.isArray(field.extraction?.labels) ? (field.extraction.labels as string[]).join(", ") : "",
    extract_pattern: typeof field.extraction?.pattern === "string" ? field.extraction.pattern : "",
    choices: (field.choices ?? [])
      .map((choice) => (choice.label === choice.value ? choice.value : `${choice.value} | ${choice.label}`))
      .join("\n"),
  };
}

/** "paid | Paid in full" or just "paid" per line. */
export function parseChoices(text: string): { value: string; label: string }[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [value = "", ...rest] = line.split("|");
      const label = rest.join("|").trim();
      return { value: value.trim(), label: label || value.trim() };
    });
}

/** "Invoice no, Invoice number" to a list, dropping blanks and repeats. */
export function parseLabels(text: string): string[] {
  return [...new Set(text.split(/[,\n]/).map((item) => item.trim()).filter(Boolean))];
}

export function toDefinition(draft: Draft): FieldDefinition {
  const validation: Record<string, unknown> = {};
  if (draft.data_type === "text") {
    if (draft.pattern.trim()) validation.pattern = draft.pattern.trim();
    if (draft.min_length.trim()) validation.min_length = Number(draft.min_length);
    if (draft.max_length.trim()) validation.max_length = Number(draft.max_length);
  }
  if (draft.data_type === "number" || draft.data_type === "date") {
    if (draft.min.trim()) validation.min = draft.min.trim();
    if (draft.max.trim()) validation.max = draft.max.trim();
  }
  const extraction: Record<string, unknown> = {};
  const labels = parseLabels(draft.labels);
  if (labels.length > 0) extraction.labels = labels;
  if (draft.extract_pattern.trim()) extraction.pattern = draft.extract_pattern.trim();
  return {
    key: draft.key.trim(),
    label: draft.label.trim(),
    data_type: draft.data_type,
    required: draft.required,
    read_only: false,
    default_value: draft.default_value.trim() || null,
    validation,
    extraction,
    choices: draft.data_type === "choice" ? parseChoices(draft.choices) : [],
  };
}

function FieldRow({
  draft,
  index,
  count,
  error,
  onChange,
  onMove,
  onRemove,
}: {
  draft: Draft;
  index: number;
  count: number;
  error: string | undefined;
  onChange: (patch: Partial<Draft>) => void;
  onMove: (by: number) => void;
  onRemove: () => void;
}) {
  const number = index + 1;
  return (
    <fieldset className="field-editor">
      <legend>Field {number}</legend>
      <div className="field-grid">
        <TextField
          label={`Label (field ${number})`}
          value={draft.label}
          onChange={(label) =>
            onChange({ label, ...(draft.keyEdited ? {} : { key: keyFromLabel(label) }) })
          }
          maxLength={120}
        />
        <TextField
          label={`Key (field ${number})`}
          value={draft.key}
          onChange={(key) => onChange({ key, keyEdited: true })}
          hint="Lowercase letters, digits and underscores. Used by integrations."
          maxLength={40}
        />
        <SelectField
          label={`Type (field ${number})`}
          value={draft.data_type}
          onChange={(data_type) => onChange({ data_type: data_type as Draft["data_type"] })}
          options={KINDS}
        />
        <TextField
          label={`Default value (field ${number})`}
          value={draft.default_value}
          onChange={(default_value) => onChange({ default_value })}
          type={draft.data_type === "date" ? "date" : "text"}
        />
      </div>
      <label className="check">
        <input
          type="checkbox"
          checked={draft.required}
          onChange={(event) => onChange({ required: event.target.checked })}
        />
        Required (field {number})
      </label>
      <div className="field-grid">
        <TextField
          label={`Labels to look for (field ${number})`}
          value={draft.labels}
          onChange={(labels) => onChange({ labels })}
          hint="Words that come just before the value on the page, separated by commas. Used to suggest values."
        />
        <TextField
          label={`Pattern to look for (field ${number})`}
          value={draft.extract_pattern}
          onChange={(extract_pattern) => onChange({ extract_pattern })}
          hint="Optional. A regular expression that finds the value anywhere, for example INV-\d{4}."
          maxLength={200}
        />
      </div>
      {draft.data_type === "text" ? (
        <div className="field-grid">
          <TextField
            label={`Must match pattern (field ${number})`}
            value={draft.pattern}
            onChange={(pattern) => onChange({ pattern })}
            hint="A regular expression for the whole value, for example INV-\d{4}."
            maxLength={200}
          />
          <TextField
            label={`Shortest (field ${number})`}
            value={draft.min_length}
            onChange={(min_length) => onChange({ min_length })}
            inputMode="numeric"
          />
          <TextField
            label={`Longest (field ${number})`}
            value={draft.max_length}
            onChange={(max_length) => onChange({ max_length })}
            inputMode="numeric"
          />
        </div>
      ) : null}
      {draft.data_type === "number" || draft.data_type === "date" ? (
        <div className="field-grid">
          <TextField
            label={`Lowest allowed (field ${number})`}
            value={draft.min}
            onChange={(min) => onChange({ min })}
            type={draft.data_type === "date" ? "date" : "text"}
            inputMode={draft.data_type === "number" ? "decimal" : "text"}
          />
          <TextField
            label={`Highest allowed (field ${number})`}
            value={draft.max}
            onChange={(max) => onChange({ max })}
            type={draft.data_type === "date" ? "date" : "text"}
            inputMode={draft.data_type === "number" ? "decimal" : "text"}
          />
        </div>
      ) : null}
      {draft.data_type === "choice" ? (
        <div className="field">
          <label htmlFor={`choices-${draft.id}`}>Choices (field {number})</label>
          <textarea
            id={`choices-${draft.id}`}
            rows={4}
            value={draft.choices}
            onChange={(event) => onChange({ choices: event.target.value })}
          />
          <p className="hint">One per line. Write “value | label” to show a different label.</p>
        </div>
      ) : null}
      {error ? (
        <p className="field-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="toolbar">
        <button type="button" className="btn btn-quiet" disabled={index === 0} onClick={() => onMove(-1)}>
          Move field {number} up
        </button>
        <button
          type="button"
          className="btn btn-quiet"
          disabled={index === count - 1}
          onClick={() => onMove(1)}
        >
          Move field {number} down
        </button>
        <button type="button" className="btn btn-quiet" disabled={count === 1} onClick={onRemove}>
          Remove field {number}
        </button>
      </div>
    </fieldset>
  );
}

/** Creates a type, or publishes a new set of fields for one. */
function TypeDialog({ type, onClose }: { type: DocumentTypeRow | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(type?.name ?? "");
  const [description, setDescription] = useState(type?.description ?? "");
  const [keywords, setKeywords] = useState((type?.keywords ?? []).join(", "));
  const [drafts, setDrafts] = useState<Draft[]>(() =>
    type && type.fields.length > 0 ? type.fields.map(toDraft) : [blank()],
  );

  const save = useMutation({
    mutationFn: () => {
      const fields = drafts.map(toDefinition);
      return type
        ? api.post<DocumentTypeRow>(`/document-types/${type.id}/versions`, {
            row_version: type.row_version,
            fields,
          })
        : api.post<DocumentTypeRow>("/document-types", {
            name: name.trim(),
            description,
            fields,
            keywords: parseLabels(keywords),
          });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["document-types"] });
      onClose();
    },
  });
  const errors = fieldErrors(save.error);
  const patch = (id: number, change: Partial<Draft>) =>
    setDrafts((current) => current.map((item) => (item.id === id ? { ...item, ...change } : item)));
  const move = (index: number, by: number) =>
    setDrafts((current) => {
      const next = [...current];
      const [item] = next.splice(index, 1);
      if (item) next.splice(index + by, 0, item);
      return next;
    });

  return (
    <Dialog title={type ? `Fields of ${type.name}` : "New document type"} onClose={onClose} wide>
      <Form onSubmit={() => save.mutate()}>
        {type ? (
          <Notice tone="info">
            Saving publishes version {(type.version ?? 0) + 1}. Documents already filled in keep the
            version they have until someone saves them again.
          </Notice>
        ) : (
          <>
            <TextField label="Name" value={name} onChange={setName} required maxLength={120} autoFocus />
            <TextField label="Description" value={description} onChange={setDescription} maxLength={1000} />
            <TextField
              label="Words that identify this type"
              value={keywords}
              onChange={setKeywords}
              hint="Separated by commas, for example invoice, supplier, total. Used to suggest the type of a document."
            />
          </>
        )}
        {drafts.map((draft, index) => (
          <FieldRow
            key={draft.id}
            draft={draft}
            index={index}
            count={drafts.length}
            error={errors[draft.key.trim() || "(no key)"]}
            onChange={(change) => patch(draft.id, change)}
            onMove={(by) => move(index, by)}
            onRemove={() => setDrafts((current) => current.filter((item) => item.id !== draft.id))}
          />
        ))}
        <div className="toolbar">
          <button type="button" className="btn" onClick={() => setDrafts((current) => [...current, blank()])}>
            Add a field
          </button>
        </div>
        <ErrorNotice error={save.error && Object.keys(errors).length === 0 ? save.error : null} />
        {Object.keys(errors).length > 0 ? (
          <Notice tone="error">Some fields are not valid. They are marked above.</Notice>
        ) : null}
        <FormActions>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={save.isPending || (!type && !name.trim())}
          >
            {save.isPending ? "Saving…" : type ? "Publish new version" : "Create type"}
          </button>
        </FormActions>
      </Form>
    </Dialog>
  );
}

function RenameDialog({ type, onClose }: { type: DocumentTypeRow; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(type.name);
  const rename = useMutation({
    mutationFn: () =>
      api.patch(`/document-types/${type.id}`, { row_version: type.row_version, name: name.trim() }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["document-types"] });
      onClose();
    },
  });
  return (
    <Dialog title={`Rename ${type.name}`} onClose={onClose}>
      <Form onSubmit={() => rename.mutate()}>
        <TextField label="Name" value={name} onChange={setName} required autoFocus maxLength={120} />
        <ErrorNotice error={rename.error} />
        <FormActions>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={rename.isPending || !name.trim() || name.trim() === type.name}
          >
            Rename
          </button>
        </FormActions>
      </Form>
    </Dialog>
  );
}

/** Keywords and confidence levels that steer how types and values are suggested. */
function RecognitionDialog({ type, onClose }: { type: DocumentTypeRow; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [keywords, setKeywords] = useState((type.keywords ?? []).join(", "));
  const [auto, setAuto] = useState(String(type.auto_accept_threshold ?? 0.9));
  const [review, setReview] = useState(String(type.review_threshold ?? 0.6));
  const save = useMutation({
    mutationFn: () =>
      api.patch(`/document-types/${type.id}`, {
        row_version: type.row_version,
        keywords: parseLabels(keywords),
        auto_accept_threshold: Number(auto),
        review_threshold: Number(review),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["document-types"] });
      onClose();
    },
  });
  return (
    <Dialog title={`Recognition for ${type.name}`} onClose={onClose}>
      <Form onSubmit={() => save.mutate()}>
        <TextField
          label="Words that identify this type"
          value={keywords}
          onChange={setKeywords}
          hint="Separated by commas."
        />
        <TextField
          label="Fill in at once from"
          value={auto}
          onChange={setAuto}
          inputMode="decimal"
          hint="A confidence from 0 to 1. At or above this, a found value is filled in without asking."
        />
        <TextField
          label="Show as a suggestion from"
          value={review}
          onChange={setReview}
          inputMode="decimal"
          hint="Below this a suggestion is marked as a weak guess. It must not be higher than the level above."
        />
        <ErrorNotice error={save.error} />
        <FormActions>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={save.isPending}>
            Save
          </button>
        </FormActions>
      </Form>
    </Dialog>
  );
}

type DialogState =
  | { kind: "create" }
  | { kind: "fields"; type: DocumentTypeRow }
  | { kind: "rename"; type: DocumentTypeRow }
  | { kind: "recognition"; type: DocumentTypeRow }
  | null;

export function DocumentTypes() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<DialogState>(null);
  const [showArchived, setShowArchived] = useState(false);
  const manage = can("document_types.manage");
  const types = useQuery({
    queryKey: ["document-types", "all", showArchived],
    queryFn: () =>
      api.get<DocumentTypeRow[]>("/document-types", { query: { include_archived: showArchived } }),
  });
  const toggle = useMutation({
    mutationFn: (type: DocumentTypeRow) =>
      api.patch(`/document-types/${type.id}`, {
        row_version: type.row_version,
        status: type.status === "active" ? "archived" : "active",
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["document-types"] }),
  });

  return (
    <div>
      <h1>Document types</h1>
      <p className="muted">
        A type says which details to record for a kind of document, such as the number and total of
        an invoice.
      </p>
      <div className="toolbar">
        {manage ? (
          <button type="button" className="btn btn-primary" onClick={() => setDialog({ kind: "create" })}>
            New document type
          </button>
        ) : null}
        <span className="grow" />
        <label className="check">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(event) => setShowArchived(event.target.checked)}
          />
          Show archived
        </label>
      </div>
      <ErrorNotice error={types.error ?? toggle.error} />
      {types.isPending ? <Spinner label="Loading document types" /> : null}
      {types.data && types.data.length === 0 ? (
        <EmptyState title="No document types">
          {manage ? "Create the first one to start describing documents." : "None have been made yet."}
        </EmptyState>
      ) : null}
      {types.data && types.data.length > 0 ? (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Document types</caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Fields</th>
                <th scope="col" className="hide-narrow">
                  Version
                </th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {types.data.map((type) => (
                <tr key={type.id}>
                  <td>
                    {type.name}
                    {type.status === "archived" ? <span className="badge badge-warn">Archived</span> : null}
                    {type.description ? <div className="muted">{type.description}</div> : null}
                  </td>
                  <td>{type.fields.map((field) => field.label).join(", ")}</td>
                  <td className="hide-narrow">{type.version}</td>
                  <td className="actions">
                    {manage ? (
                      <ActionMenu label={`Actions for ${type.name}`}>
                        {type.status === "active" ? (
                          <button type="button" onClick={() => setDialog({ kind: "fields", type })}>
                            Change fields…
                          </button>
                        ) : null}
                        <button type="button" onClick={() => setDialog({ kind: "recognition", type })}>
                          Recognition…
                        </button>
                        <button type="button" onClick={() => setDialog({ kind: "rename", type })}>
                          Rename…
                        </button>
                        <button type="button" onClick={() => toggle.mutate(type)}>
                          {type.status === "active" ? "Archive" : "Restore"}
                        </button>
                      </ActionMenu>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {dialog?.kind === "create" ? <TypeDialog type={null} onClose={() => setDialog(null)} /> : null}
      {dialog?.kind === "fields" ? (
        <TypeDialog type={dialog.type} onClose={() => setDialog(null)} />
      ) : null}
      {dialog?.kind === "recognition" ? (
        <RecognitionDialog type={dialog.type} onClose={() => setDialog(null)} />
      ) : null}
      {dialog?.kind === "rename" ? <RenameDialog type={dialog.type} onClose={() => setDialog(null)} /> : null}
    </div>
  );
}
