import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { CommentRow, Person, TaskRow } from "../../api/types";
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

const PRIORITIES = [
  { value: "low", label: "Low" },
  { value: "normal", label: "Normal" },
  { value: "high", label: "High" },
];

/** "2026-12-01" from a date input to a time the API accepts, at the end of that day. */
export function dueFromDate(value: string): string | null {
  return value ? `${value}T23:59:00Z` : null;
}

/** Comments and tasks on one document. */
export function DiscussionDialog({
  documentId,
  title,
  canComment,
  canAssign,
  onClose,
}: {
  documentId: string;
  title: string;
  canComment: boolean;
  canAssign: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [body, setBody] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [assignee, setAssignee] = useState("");
  const [priority, setPriority] = useState("normal");
  const [due, setDue] = useState("");

  const comments = useQuery({
    queryKey: ["comments", documentId],
    queryFn: () => api.get<CommentRow[]>(`/documents/${documentId}/comments`),
  });
  const tasks = useQuery({
    queryKey: ["doc-tasks", documentId],
    queryFn: () => api.get<TaskRow[]>(`/documents/${documentId}/tasks`),
  });
  const people = useQuery({
    queryKey: ["assignable", documentId],
    enabled: canAssign,
    queryFn: () => api.get<Person[]>(`/documents/${documentId}/assignable`),
  });
  const reload = (key: string) => () =>
    void queryClient.invalidateQueries({ queryKey: [key, documentId] });

  const post = useMutation({
    mutationFn: () => api.post(`/documents/${documentId}/comments`, { body }),
    onSuccess: () => {
      setBody("");
      reload("comments")();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/documents/${documentId}/comments/${id}`),
    onSuccess: reload("comments"),
  });
  const create = useMutation({
    mutationFn: () =>
      api.post(`/documents/${documentId}/tasks`, {
        title: taskTitle,
        assignee_id: assignee,
        priority,
        due_at: dueFromDate(due),
      }),
    onSuccess: () => {
      setTaskTitle("");
      setDue("");
      reload("doc-tasks")();
    },
  });
  const settle = useMutation({
    mutationFn: (input: { task: TaskRow; status: string }) =>
      api.patch(`/tasks/${input.task.id}`, {
        row_version: input.task.row_version,
        status: input.status,
      }),
    onSuccess: () => {
      reload("doc-tasks")();
      void queryClient.invalidateQueries({ queryKey: ["my-tasks"] });
    },
  });

  return (
    <Dialog title={`Discussion of ${title}`} onClose={onClose} wide>
      <h3>Tasks</h3>
      {tasks.isPending ? <Spinner label="Loading tasks" /> : null}
      <ErrorNotice error={tasks.error ?? settle.error} />
      {tasks.data && tasks.data.length === 0 ? (
        <p className="muted">No tasks on this document.</p>
      ) : null}
      <ul className="task-list">
        {(tasks.data ?? []).map((task) => (
          <li key={task.id}>
            <strong>{task.title}</strong> <span className="badge">{task.status}</span>
            {task.priority === "high" ? <span className="badge badge-warn">High</span> : null}
            <div className="muted">
              For {task.assignee_name}
              {task.due_at ? `, due ${formatDate(task.due_at)}` : ""}
            </div>
            {task.status === "open" && task.can_change ? (
              <button
                type="button"
                className="btn btn-quiet"
                disabled={settle.isPending}
                onClick={() => settle.mutate({ task, status: "done" })}
              >
                Mark done<span className="sr-only"> {task.title}</span>
              </button>
            ) : null}
          </li>
        ))}
      </ul>
      {canAssign ? (
        <Form onSubmit={() => create.mutate()}>
          <h4>Give someone a task</h4>
          <TextField label="Task" value={taskTitle} onChange={setTaskTitle} maxLength={200} />
          <SelectField
            label="For"
            value={assignee}
            onChange={setAssignee}
            options={(people.data ?? []).map((p) => ({ value: p.id, label: p.name }))}
            placeholder="Choose a person"
          />
          <SelectField
            label="Priority"
            value={priority}
            onChange={setPriority}
            options={PRIORITIES}
          />
          <TextField label="Due date (optional)" value={due} onChange={setDue} type="date" />
          <ErrorNotice error={create.error} />
          <FormActions>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={create.isPending || !taskTitle.trim() || !assignee}
            >
              Add task
            </button>
          </FormActions>
        </Form>
      ) : null}

      <h3>Comments</h3>
      {comments.isPending ? <Spinner label="Loading comments" /> : null}
      <ErrorNotice error={comments.error ?? remove.error} />
      {comments.data && comments.data.length === 0 ? (
        <p className="muted">No comments yet.</p>
      ) : null}
      <ul className="comment-list">
        {(comments.data ?? []).map((item) => (
          <li key={item.id}>
            <div className="muted">
              {item.author_name}, {formatDate(item.created_at)}
            </div>
            <p>{item.body}</p>
            {item.can_delete ? (
              <button
                type="button"
                className="btn btn-quiet"
                onClick={() => remove.mutate(item.id)}
              >
                Remove<span className="sr-only"> comment by {item.author_name}</span>
              </button>
            ) : null}
          </li>
        ))}
      </ul>
      {canComment ? (
        <Form onSubmit={() => post.mutate()}>
          <div className="field">
            <label htmlFor="new-comment">Add a comment</label>
            <textarea
              id="new-comment"
              rows={3}
              value={body}
              maxLength={4000}
              onChange={(event) => setBody(event.target.value)}
            />
            <p className="hint">
              Write @name, the part of an email before the @, to mention someone.
            </p>
          </div>
          <ErrorNotice error={post.error} />
          <FormActions>
            <button type="button" className="btn" onClick={onClose}>
              Done
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={post.isPending || !body.trim()}
            >
              Comment
            </button>
          </FormActions>
        </Form>
      ) : (
        <>
          <Notice tone="info">You can read this discussion but not add to it.</Notice>
          <FormActions>
            <button type="button" className="btn" onClick={onClose}>
              Done
            </button>
          </FormActions>
        </>
      )}
    </Dialog>
  );
}
