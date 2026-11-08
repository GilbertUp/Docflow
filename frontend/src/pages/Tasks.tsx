import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { DelegationRow, PersonRow, TaskRow } from "../api/types";
import {
  EmptyState,
  ErrorNotice,
  Form,
  FormActions,
  SelectField,
  Spinner,
  TextField,
  formatDate,
} from "../components/ui";

/** Whether an open task is past its due time. */
export function isOverdue(
  task: Pick<TaskRow, "status" | "due_at">,
  now: Date = new Date(),
): boolean {
  return task.status === "open" && task.due_at !== null && new Date(task.due_at) < now;
}

/** A date input value at the start of that day in the person's own time. */
export function startOfDay(value: string): string {
  return new Date(`${value}T00:00:00`).toISOString();
}

/** A date input value at the end of that day in the person's own time. */
export function endOfDay(value: string): string {
  return new Date(`${value}T23:59:59`).toISOString();
}

/** Lets someone else take new workflow steps while a person is away. */
function Delegation() {
  const queryClient = useQueryClient();
  const [to, setTo] = useState("");
  const [from, setFrom] = useState("");
  const [until, setUntil] = useState("");
  const people = useQuery({ queryKey: ["people"], queryFn: () => api.get<PersonRow[]>("/people") });
  const current = useQuery({
    queryKey: ["delegations"],
    queryFn: () => api.get<DelegationRow[]>("/delegations"),
  });
  const reload = () => void queryClient.invalidateQueries({ queryKey: ["delegations"] });
  const add = useMutation({
    mutationFn: () =>
      api.post("/delegations", {
        to_user_id: to,
        starts_at: startOfDay(from),
        ends_at: endOfDay(until),
      }),
    onSuccess: () => {
      setTo("");
      reload();
    },
  });
  const end = useMutation({
    mutationFn: (id: string) => api.delete(`/delegations/${id}`),
    onSuccess: reload,
  });
  return (
    <section>
      <h2>While I am away</h2>
      <p className="muted">New workflow steps for you go to the person you choose, between these dates.</p>
      <ErrorNotice error={current.error ?? end.error} />
      <ul className="task-list">
        {(current.data ?? []).map((item) => (
          <li key={item.id}>
            To {item.to_name}, {formatDate(item.starts_at)} until {formatDate(item.ends_at)}
            <button type="button" className="btn btn-quiet" onClick={() => end.mutate(item.id)}>
              End<span className="sr-only"> delegation to {item.to_name}</span>
            </button>
          </li>
        ))}
      </ul>
      <Form onSubmit={() => add.mutate()}>
        <SelectField
          label="Hand my steps to"
          value={to}
          onChange={setTo}
          options={(people.data ?? []).map((p) => ({ value: p.id, label: p.name }))}
          placeholder="Choose a person"
        />
        <TextField label="From" value={from} onChange={setFrom} type="date" />
        <TextField label="Until" value={until} onChange={setUntil} type="date" />
        <ErrorNotice error={add.error} />
        <FormActions>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={add.isPending || !to || !from || !until}
          >
            Delegate
          </button>
        </FormActions>
      </Form>
    </section>
  );
}

export function Tasks() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("open");
  const tasks = useQuery({
    queryKey: ["my-tasks", status],
    queryFn: () => api.get<TaskRow[]>("/tasks", { query: { status, mine: true } }),
  });
  const settle = useMutation({
    mutationFn: (task: TaskRow) =>
      api.patch(`/tasks/${task.id}`, { row_version: task.row_version, status: "done" }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["my-tasks"] }),
  });

  return (
    <div>
      <h1>My tasks</h1>
      <div className="toolbar">
        <label>
          Show{" "}
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="open">Open</option>
            <option value="done">Done</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </label>
      </div>
      <ErrorNotice error={tasks.error ?? settle.error} />
      {tasks.isPending ? <Spinner label="Loading tasks" /> : null}
      {tasks.data && tasks.data.length === 0 ? (
        <EmptyState title="Nothing here">No {status} tasks are waiting for you.</EmptyState>
      ) : null}
      {tasks.data && tasks.data.length > 0 ? (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Tasks for you</caption>
            <thead>
              <tr>
                <th scope="col">Task</th>
                <th scope="col">Document</th>
                <th scope="col">Due</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {tasks.data.map((task) => (
                <tr key={task.id}>
                  <td>
                    {task.title}
                    {task.priority === "high" ? (
                      <span className="badge badge-warn">High</span>
                    ) : null}
                    {isOverdue(task) ? <span className="badge badge-warn">Overdue</span> : null}
                  </td>
                  <td>
                    <Link to={`/search?q=${encodeURIComponent(task.document_title)}`}>
                      {task.document_title}
                    </Link>
                  </td>
                  <td>{formatDate(task.due_at)}</td>
                  <td className="actions">
                    {task.status === "open" && task.can_change ? (
                      <button
                        type="button"
                        className="btn btn-quiet"
                        disabled={settle.isPending}
                        onClick={() => settle.mutate(task)}
                      >
                        Mark done<span className="sr-only"> {task.title}</span>
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <Delegation />
    </div>
  );
}
