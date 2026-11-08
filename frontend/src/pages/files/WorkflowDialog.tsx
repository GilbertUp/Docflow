import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { WorkflowInstance, WorkflowRow } from "../../api/types";
import {
  Dialog,
  ErrorNotice,
  Form,
  FormActions,
  Notice,
  SelectField,
  Spinner,
  TextField,
  formatDate,
} from "../../components/ui";

const STATUS: Record<string, string> = {
  pending: "Waiting",
  active: "Needs a decision",
  approved: "Approved",
  rejected: "Rejected",
  done: "Done",
  skipped: "Skipped",
};

const RUN_STATUS: Record<string, string> = {
  running: "In progress",
  completed: "Finished",
  rejected: "Rejected",
  cancelled: "Stopped",
};

/** The wording of one history entry. */
export function eventText(kind: string, details: Record<string, unknown>): string {
  switch (kind) {
    case "started":
      return details.automatic ? "Started by itself" : "Started";
    case "step_activated":
      return "Step began";
    case "decided":
      return `Decided: ${String(details.decision ?? "")}`;
    case "completed":
      return "Finished";
    case "rejected":
      return "Rejected";
    case "cancelled":
      return "Stopped";
    default:
      return kind;
  }
}

/** Workflows on one document: what is running, who it waits for, and a way to start one. */
export function WorkflowDialog({
  documentId,
  title,
  canStart,
  userId,
  onClose,
}: {
  documentId: string;
  title: string;
  canStart: boolean;
  userId: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [choice, setChoice] = useState("");
  const [comment, setComment] = useState("");
  const instances = useQuery({
    queryKey: ["doc-workflows", documentId],
    queryFn: () => api.get<WorkflowInstance[]>(`/documents/${documentId}/workflows`),
  });
  const available = useQuery({
    queryKey: ["workflows", "active"],
    enabled: canStart,
    queryFn: () => api.get<WorkflowRow[]>("/workflows"),
  });
  const reload = () => {
    void queryClient.invalidateQueries({ queryKey: ["doc-workflows", documentId] });
    void queryClient.invalidateQueries({ queryKey: ["my-tasks"] });
  };
  const start = useMutation({
    mutationFn: () => api.post(`/documents/${documentId}/workflows`, { workflow_id: choice }),
    onSuccess: () => {
      setChoice("");
      reload();
    },
  });
  const decide = useMutation({
    mutationFn: (input: { step: string; decision: string }) =>
      api.post(`/workflow-steps/${input.step}/decision`, {
        decision: input.decision,
        comment,
      }),
    onSuccess: () => {
      setComment("");
      reload();
    },
  });
  const stop = useMutation({
    mutationFn: (id: string) => api.post(`/workflow-instances/${id}/cancel`, {}),
    onSuccess: reload,
  });

  return (
    <Dialog title={`Workflow of ${title}`} onClose={onClose} wide>
      {instances.isPending ? <Spinner label="Loading workflows" /> : null}
      <ErrorNotice error={instances.error ?? decide.error ?? stop.error} />
      {instances.data && instances.data.length === 0 ? (
        <p className="muted">No workflow has been run on this document.</p>
      ) : null}
      {(instances.data ?? []).map((instance) => {
        const waiting = instance.steps.find(
          (step) => step.status === "active" && step.assignee_id === userId,
        );
        return (
          <section key={instance.id} className="workflow-run">
            <h3>
              {instance.workflow_name} <span className="badge">{RUN_STATUS[instance.status]}</span>
            </h3>
            <ol className="workflow-steps">
              {instance.steps.map((step) => (
                <li key={step.id}>
                  <strong>{step.name}</strong> ({step.kind}) for {step.assignee_name}
                  {step.delegated_from ? `, delegated from ${step.delegated_from}` : ""}:{" "}
                  {STATUS[step.status] ?? step.status}
                  {step.comment ? <div className="muted">“{step.comment}”</div> : null}
                </li>
              ))}
            </ol>
            {waiting ? (
              <Form
                onSubmit={() =>
                  decide.mutate({
                    step: waiting.id,
                    decision: waiting.kind === "approval" ? "approve" : "complete",
                  })
                }
              >
                <TextField
                  label={`Comment on ${waiting.name} (needed to reject)`}
                  value={comment}
                  onChange={setComment}
                  maxLength={2000}
                />
                <FormActions>
                  {waiting.kind === "approval" ? (
                    <>
                      <button
                        type="button"
                        className="btn"
                        disabled={decide.isPending || !comment.trim()}
                        onClick={() => decide.mutate({ step: waiting.id, decision: "reject" })}
                      >
                        Reject
                      </button>
                      <button type="submit" className="btn btn-primary" disabled={decide.isPending}>
                        Approve
                      </button>
                    </>
                  ) : (
                    <button type="submit" className="btn btn-primary" disabled={decide.isPending}>
                      Mark complete
                    </button>
                  )}
                </FormActions>
              </Form>
            ) : null}
            {instance.can_cancel ? (
              <button
                type="button"
                className="btn btn-quiet"
                disabled={stop.isPending}
                onClick={() => stop.mutate(instance.id)}
              >
                Stop this workflow
              </button>
            ) : null}
            <details>
              <summary>History</summary>
              <ul>
                {instance.events.map((event, index) => (
                  <li key={index}>
                    {formatDate(event.created_at)}: {eventText(event.kind, event.details)}
                  </li>
                ))}
              </ul>
            </details>
          </section>
        );
      })}
      {canStart ? (
        <Form onSubmit={() => start.mutate()}>
          <h3>Start a workflow</h3>
          {available.data && available.data.length === 0 ? (
            <Notice tone="info">No workflows exist yet. Someone who manages workflows can add them.</Notice>
          ) : null}
          <SelectField
            label="Workflow"
            value={choice}
            onChange={setChoice}
            options={(available.data ?? []).map((w) => ({ value: w.id, label: w.name }))}
            placeholder="Choose a workflow"
          />
          <ErrorNotice error={start.error} />
          <FormActions>
            <button type="button" className="btn" onClick={onClose}>
              Done
            </button>
            <button type="submit" className="btn btn-primary" disabled={!choice || start.isPending}>
              Start
            </button>
          </FormActions>
        </Form>
      ) : (
        <FormActions>
          <button type="button" className="btn" onClick={onClose}>
            Done
          </button>
        </FormActions>
      )}
    </Dialog>
  );
}
