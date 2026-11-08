import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, fieldErrors } from "../../api/client";
import type { DocumentTypeRow, PersonRow, SlaPolicyRow, WorkflowRow } from "../../api/types";
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

interface StepDraft {
  id: number;
  name: string;
  kind: "approval" | "task";
  assignee: string;
  days: string;
}

let nextId = 1;

function blank(): StepDraft {
  return { id: nextId++, name: "", kind: "approval", assignee: "", days: "" };
}

/** The steps as the API takes them. Exported so the mapping can be tested. */
export function toSteps(drafts: StepDraft[]) {
  return drafts.map((draft) => ({
    name: draft.name.trim(),
    kind: draft.kind,
    assignee_id: draft.assignee,
    due_days: draft.days.trim() === "" ? null : Number(draft.days),
  }));
}

/** A trigger from the chosen type, or none for a workflow that is started by hand. */
export function toTrigger(typeId: string): Record<string, string> {
  return typeId ? { event: "document.typed", document_type_id: typeId } : {};
}

function WorkflowDialog({ workflow, onClose }: { workflow: WorkflowRow | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(workflow?.name ?? "");
  const [typeId, setTypeId] = useState(String(workflow?.trigger?.document_type_id ?? ""));
  const [drafts, setDrafts] = useState<StepDraft[]>(() =>
    workflow
      ? workflow.steps.map((step) => ({
          id: nextId++,
          name: step.name,
          kind: step.kind,
          assignee: step.assignee_id,
          days: step.due_days === null || step.due_days === undefined ? "" : String(step.due_days),
        }))
      : [blank()],
  );
  const people = useQuery({
    queryKey: ["people"],
    queryFn: () => api.get<PersonRow[]>("/people"),
  });
  const types = useQuery({
    queryKey: ["document-types"],
    queryFn: () => api.get<DocumentTypeRow[]>("/document-types"),
  });
  const save = useMutation({
    mutationFn: () => {
      const body = { steps: toSteps(drafts), trigger: toTrigger(typeId) };
      return workflow
        ? api.post(`/workflows/${workflow.id}/versions`, { ...body, row_version: workflow.row_version })
        : api.post("/workflows", { ...body, name: name.trim() });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["workflows"] });
      onClose();
    },
  });
  const errors = fieldErrors(save.error);
  const patch = (id: number, change: Partial<StepDraft>) =>
    setDrafts((current) => current.map((item) => (item.id === id ? { ...item, ...change } : item)));
  const move = (index: number, by: number) =>
    setDrafts((current) => {
      const next = [...current];
      const [item] = next.splice(index, 1);
      if (item) next.splice(index + by, 0, item);
      return next;
    });
  const options = (people.data ?? []).map((p) => ({ value: p.id, label: p.name }));

  return (
    <Dialog title={workflow ? `Steps of ${workflow.name}` : "New workflow"} onClose={onClose} wide>
      <Form onSubmit={() => save.mutate()}>
        {workflow ? (
          <Notice tone="info">
            Saving publishes version {workflow.version + 1}. Work already started keeps the version it
            began on.
          </Notice>
        ) : (
          <TextField label="Name" value={name} onChange={setName} required maxLength={120} autoFocus />
        )}
        <SelectField
          label="Start by itself when a document is given this type"
          value={typeId}
          onChange={setTypeId}
          options={(types.data ?? []).map((t) => ({ value: t.id, label: t.name }))}
          placeholder="Never, it is started by hand"
        />
        {drafts.map((draft, index) => {
          const number = index + 1;
          return (
            <fieldset className="field-editor" key={draft.id}>
              <legend>Step {number}</legend>
              <div className="field-grid">
                <TextField
                  label={`Name (step ${number})`}
                  value={draft.name}
                  onChange={(value) => patch(draft.id, { name: value })}
                  maxLength={120}
                />
                <SelectField
                  label={`Kind (step ${number})`}
                  value={draft.kind}
                  onChange={(value) => patch(draft.id, { kind: value as StepDraft["kind"] })}
                  options={[
                    { value: "approval", label: "Approval" },
                    { value: "task", label: "Task" },
                  ]}
                />
                <SelectField
                  label={`For (step ${number})`}
                  value={draft.assignee}
                  onChange={(value) => patch(draft.id, { assignee: value })}
                  options={options}
                  placeholder="Choose a person"
                />
                <TextField
                  label={`Days to finish (step ${number})`}
                  value={draft.days}
                  onChange={(value) => patch(draft.id, { days: value })}
                  inputMode="numeric"
                />
              </div>
              {errors[`step ${number}`] ? (
                <p className="field-error" role="alert">
                  {errors[`step ${number}`]}
                </p>
              ) : null}
              <div className="toolbar">
                <button type="button" className="btn btn-quiet" disabled={index === 0} onClick={() => move(index, -1)}>
                  Move step {number} up
                </button>
                <button
                  type="button"
                  className="btn btn-quiet"
                  disabled={index === drafts.length - 1}
                  onClick={() => move(index, 1)}
                >
                  Move step {number} down
                </button>
                <button
                  type="button"
                  className="btn btn-quiet"
                  disabled={drafts.length === 1}
                  onClick={() => setDrafts((current) => current.filter((item) => item.id !== draft.id))}
                >
                  Remove step {number}
                </button>
              </div>
            </fieldset>
          );
        })}
        <div className="toolbar">
          <button type="button" className="btn" onClick={() => setDrafts((c) => [...c, blank()])}>
            Add a step
          </button>
        </div>
        <ErrorNotice error={save.error && Object.keys(errors).length === 0 ? save.error : null} />
        {Object.keys(errors).length > 0 ? (
          <Notice tone="error">Some steps are not valid. They are marked above.</Notice>
        ) : null}
        <FormActions>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={save.isPending || (!workflow && !name.trim())}
          >
            {save.isPending ? "Saving…" : workflow ? "Publish new version" : "Create workflow"}
          </button>
        </FormActions>
      </Form>
    </Dialog>
  );
}

/** Hours as the API takes them: blank is none. */
export function hoursOrNull(text: string): number | null {
  const clean = text.trim();
  return clean === "" ? null : Number(clean);
}

function SlaPanel() {
  const queryClient = useQueryClient();
  const policy = useQuery({ queryKey: ["sla"], queryFn: () => api.get<SlaPolicyRow>("/sla-policy") });
  const people = useQuery({ queryKey: ["people"], queryFn: () => api.get<PersonRow[]>("/people") });
  const [reminder, setReminder] = useState<string | null>(null);
  const [after, setAfter] = useState<string | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      api.put("/sla-policy", {
        reminder_before_hours: Number(reminder ?? policy.data?.reminder_before_hours ?? 0),
        escalate_after_hours: hoursOrNull(after ?? String(policy.data?.escalate_after_hours ?? "")),
        escalate_to_user_id: (target ?? policy.data?.escalate_to_user_id ?? "") || null,
      }),
    onSuccess: () => {
      setReminder(null);
      setAfter(null);
      setTarget(null);
      void queryClient.invalidateQueries({ queryKey: ["sla"] });
    },
  });
  if (!policy.data) return null;
  return (
    <section>
      <h2>Deadlines</h2>
      <p className="muted">
        Tasks with a due time remind the person, and can tell someone else when they are overdue.
      </p>
      <Form onSubmit={() => save.mutate()}>
        <TextField
          label="Remind this many hours before it is due (0 for never)"
          value={reminder ?? String(policy.data.reminder_before_hours)}
          onChange={setReminder}
          inputMode="numeric"
        />
        <TextField
          label="Tell someone this many hours after it is due (blank for never)"
          value={after ?? String(policy.data.escalate_after_hours ?? "")}
          onChange={setAfter}
          inputMode="numeric"
        />
        <SelectField
          label="Who to tell"
          value={target ?? policy.data.escalate_to_user_id ?? ""}
          onChange={setTarget}
          options={(people.data ?? []).map((p) => ({ value: p.id, label: p.name }))}
          placeholder="Whoever made the task"
        />
        <ErrorNotice error={save.error} />
        <FormActions>
          <button type="submit" className="btn btn-primary" disabled={save.isPending}>
            Save deadlines
          </button>
        </FormActions>
      </Form>
    </section>
  );
}

type DialogState = { kind: "create" } | { kind: "edit"; workflow: WorkflowRow } | null;

export function Workflows() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<DialogState>(null);
  const manage = can("workflows.manage");
  const list = useQuery({
    queryKey: ["workflows"],
    queryFn: () => api.get<WorkflowRow[]>("/workflows", { query: { include_archived: true } }),
  });
  const toggle = useMutation({
    mutationFn: (workflow: WorkflowRow) =>
      api.patch(`/workflows/${workflow.id}`, {
        row_version: workflow.row_version,
        status: workflow.status === "active" ? "archived" : "active",
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["workflows"] }),
  });

  return (
    <div>
      <h1>Workflows</h1>
      <p className="muted">
        A workflow sends a document through steps of approval and work, one after another.
      </p>
      <div className="toolbar">
        {manage ? (
          <button type="button" className="btn btn-primary" onClick={() => setDialog({ kind: "create" })}>
            New workflow
          </button>
        ) : null}
      </div>
      <ErrorNotice error={list.error ?? toggle.error} />
      {list.isPending ? <Spinner label="Loading workflows" /> : null}
      {list.data && list.data.length === 0 ? (
        <EmptyState title="No workflows">
          {manage ? "Create the first one to route documents." : "None have been made yet."}
        </EmptyState>
      ) : null}
      {list.data && list.data.length > 0 ? (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Workflows</caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Steps</th>
                <th scope="col" className="hide-narrow">
                  Version
                </th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {list.data.map((workflow) => (
                <tr key={workflow.id}>
                  <td>
                    {workflow.name}
                    {workflow.status === "archived" ? <span className="badge badge-warn">Archived</span> : null}
                    {workflow.trigger?.event ? <span className="badge">Starts by itself</span> : null}
                  </td>
                  <td>{workflow.steps.map((step) => step.name).join(" → ")}</td>
                  <td className="hide-narrow">{workflow.version}</td>
                  <td className="actions">
                    {manage ? (
                      <ActionMenu label={`Actions for ${workflow.name}`}>
                        {workflow.status === "active" ? (
                          <button type="button" onClick={() => setDialog({ kind: "edit", workflow })}>
                            Change steps…
                          </button>
                        ) : null}
                        <button type="button" onClick={() => toggle.mutate(workflow)}>
                          {workflow.status === "active" ? "Archive" : "Restore"}
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
      {manage ? <SlaPanel /> : null}
      {dialog?.kind === "create" ? <WorkflowDialog workflow={null} onClose={() => setDialog(null)} /> : null}
      {dialog?.kind === "edit" ? (
        <WorkflowDialog workflow={dialog.workflow} onClose={() => setDialog(null)} />
      ) : null}
    </div>
  );
}
