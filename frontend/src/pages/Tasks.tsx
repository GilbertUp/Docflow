import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { TaskRow } from "../api/types";
import { EmptyState, ErrorNotice, Spinner, formatDate } from "../components/ui";

/** Whether an open task is past its due time. */
export function isOverdue(
  task: Pick<TaskRow, "status" | "due_at">,
  now: Date = new Date(),
): boolean {
  return task.status === "open" && task.due_at !== null && new Date(task.due_at) < now;
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
    </div>
  );
}
