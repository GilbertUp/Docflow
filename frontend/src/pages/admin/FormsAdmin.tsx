import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, fieldErrors } from "../../api/client";
import type { FormRow } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import {
  ActionMenu,
  Dialog,
  EmptyState,
  ErrorNotice,
  Form,
  FormActions,
  Notice,
  Spinner,
  TextField,
} from "../../components/ui";
import { FieldRow, blank, toDefinition, toDraft, type Draft } from "./DocumentTypes";

/** What the form API takes for a field: the definition without document-type extras. */
export function toFormField(draft: Draft) {
  const { key, label, data_type, required, default_value, validation, choices } = toDefinition(draft);
  return { key, label, data_type, required, default_value, validation, choices };
}

function FormDialog({ form, onClose }: { form: FormRow | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(form?.name ?? "");
  const [description, setDescription] = useState(form?.description ?? "");
  const [drafts, setDrafts] = useState<Draft[]>(() =>
    form && form.fields.length > 0 ? form.fields.map((f) => toDraft({ ...f, read_only: false })) : [blank()],
  );
  const save = useMutation({
    mutationFn: () => {
      const fields = drafts.map(toFormField);
      return form
        ? api.post(`/forms/${form.id}/versions`, { row_version: form.row_version, fields })
        : api.post("/forms", { name: name.trim(), description, fields });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["forms"] });
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
    <Dialog title={form ? `Fields of ${form.name}` : "New form"} onClose={onClose} wide>
      <Form onSubmit={() => save.mutate()}>
        {form ? (
          <Notice tone="info">
            Saving publishes version {form.version + 1}. Earlier submissions keep the version they were made
            on.
          </Notice>
        ) : (
          <>
            <TextField label="Name" value={name} onChange={setName} required maxLength={120} autoFocus />
            <TextField label="Description" value={description} onChange={setDescription} maxLength={1000} />
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
            disabled={save.isPending || (!form && !name.trim())}
          >
            {save.isPending ? "Saving…" : form ? "Publish new version" : "Create form"}
          </button>
        </FormActions>
      </Form>
    </Dialog>
  );
}

type DialogState = { kind: "create" } | { kind: "edit"; form: FormRow } | null;

export function FormsAdmin() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<DialogState>(null);
  const manage = can("forms.manage");
  const list = useQuery({
    queryKey: ["forms", "all"],
    queryFn: () => api.get<FormRow[]>("/forms", { query: { include_archived: true } }),
  });
  const toggle = useMutation({
    mutationFn: (form: FormRow) =>
      api.patch(`/forms/${form.id}`, {
        row_version: form.row_version,
        status: form.status === "active" ? "archived" : "active",
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["forms"] }),
  });

  return (
    <div>
      <h1>Manage forms</h1>
      <p className="muted">A form collects the same details every time, such as a leave request.</p>
      <div className="toolbar">
        {manage ? (
          <button type="button" className="btn btn-primary" onClick={() => setDialog({ kind: "create" })}>
            New form
          </button>
        ) : null}
      </div>
      <ErrorNotice error={list.error ?? toggle.error} />
      {list.isPending ? <Spinner label="Loading forms" /> : null}
      {list.data && list.data.length === 0 ? (
        <EmptyState title="No forms">{manage ? "Create the first one." : "None have been made yet."}</EmptyState>
      ) : null}
      {list.data && list.data.length > 0 ? (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Forms</caption>
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
              {list.data.map((form) => (
                <tr key={form.id}>
                  <td>
                    {form.name}
                    {form.status === "archived" ? <span className="badge badge-warn">Archived</span> : null}
                  </td>
                  <td>{form.fields.map((field) => field.label).join(", ")}</td>
                  <td className="hide-narrow">{form.version}</td>
                  <td className="actions">
                    {manage ? (
                      <ActionMenu label={`Actions for ${form.name}`}>
                        {form.status === "active" ? (
                          <button type="button" onClick={() => setDialog({ kind: "edit", form })}>
                            Change fields…
                          </button>
                        ) : null}
                        <button type="button" onClick={() => toggle.mutate(form)}>
                          {form.status === "active" ? "Archive" : "Restore"}
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
      {dialog?.kind === "create" ? <FormDialog form={null} onClose={() => setDialog(null)} /> : null}
      {dialog?.kind === "edit" ? <FormDialog form={dialog.form} onClose={() => setDialog(null)} /> : null}
    </div>
  );
}
