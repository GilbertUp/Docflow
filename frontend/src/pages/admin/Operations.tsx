import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { AdminAnalytics, AdminHealth, AdminJob, JobSummary } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ErrorNotice, Notice, Spinner, formatBytes, formatDate } from "../../components/ui";

/** The height of a bar, as a percentage of the busiest day, with a sliver for any activity. */
export function barHeight(count: number, peak: number): number {
  if (count <= 0 || peak <= 0) return 0;
  return Math.max(4, Math.round((count / peak) * 100));
}

/** How long a wait is, in the largest sensible unit. */
export function waitText(seconds: number | null): string {
  if (seconds === null) return "nothing is waiting";
  if (seconds < 90) return `${seconds} seconds`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} minutes`;
  return `${Math.round(seconds / 3600)} hours`;
}

const CHECK_NAMES: Record<string, string> = {
  database: "Database",
  queue: "Job queue",
  storage: "File storage",
  workers: "Background workers",
  job_backlog: "Waiting jobs",
  dead_jobs: "Jobs that gave up",
  audit_checkpoint: "Audit trail anchor",
};

function Bars({ title, data }: { title: string; data: { day: string; count: number }[] }) {
  const peak = Math.max(0, ...data.map((d) => d.count));
  const total = data.reduce((sum, d) => sum + d.count, 0);
  return (
    <figure className="bars">
      <figcaption>
        {title}: {total} in {data.length} days
      </figcaption>
      <div className="bar-row" role="img" aria-label={`${title} per day, busiest day ${peak}`}>
        {data.map((d) => (
          <span
            key={d.day}
            className="bar"
            style={{ height: `${barHeight(d.count, peak)}%` }}
            title={`${d.day}: ${d.count}`}
          />
        ))}
      </div>
    </figure>
  );
}

function Health() {
  const health = useQuery({
    queryKey: ["admin-health"],
    queryFn: () => api.get<AdminHealth>("/admin/health"),
    refetchInterval: 30_000,
  });
  return (
    <section>
      <h2>Health</h2>
      <ErrorNotice error={health.error} />
      {health.isPending ? <Spinner label="Checking" /> : null}
      {health.data ? (
        <ul className="health">
          {health.data.checks.map((check) => (
            <li key={check.name} className={check.ok ? "ok" : "bad"}>
              <strong>{CHECK_NAMES[check.name] ?? check.name}</strong>
              <span className={check.ok ? "badge badge-ok" : "badge badge-warn"}>
                {check.ok ? "Fine" : "Look at this"}
              </span>
              <div className="muted">{check.detail}</div>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function Usage() {
  const [days, setDays] = useState(30);
  const usage = useQuery({
    queryKey: ["admin-analytics", days],
    queryFn: () => api.get<AdminAnalytics>("/admin/analytics", { query: { days } }),
  });
  const a = usage.data;
  return (
    <section>
      <h2>Usage</h2>
      <label>
        Period{" "}
        <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
          <option value={7}>7 days</option>
          <option value={30}>30 days</option>
          <option value={90}>90 days</option>
        </select>
      </label>
      <ErrorNotice error={usage.error} />
      {usage.isPending ? <Spinner label="Counting" /> : null}
      {a ? (
        <>
          <dl className="figures">
            <div><dt>Documents</dt><dd>{a.documents}</dd></div>
            <div><dt>Versions</dt><dd>{a.versions}</dd></div>
            <div><dt>People</dt><dd>{a.active_users}</dd></div>
            <div><dt>Stored files</dt><dd>{formatBytes(a.stored_bytes)}</dd></div>
            <div><dt>Smaller copies</dt><dd>{formatBytes(a.copy_bytes)}</dd></div>
            <div><dt>Open tasks</dt><dd>{a.open_tasks}</dd></div>
            <div><dt>Overdue</dt><dd>{a.overdue_tasks}</dd></div>
            <div><dt>On legal hold</dt><dd>{a.documents_on_hold}</dd></div>
            <div><dt>Without a type</dt><dd>{a.untyped_documents}</dd></div>
          </dl>
          <Bars title="Documents filed" data={a.uploads} />
          <Bars title="Searches" data={a.searches} />
          <Bars title="Questions asked" data={a.questions} />
          <h3>By cabinet</h3>
          <div className="table-wrap">
            <table>
              <caption className="sr-only">Documents and size by cabinet</caption>
              <thead>
                <tr>
                  <th scope="col">Cabinet</th>
                  <th scope="col">Documents</th>
                  <th scope="col">Size</th>
                </tr>
              </thead>
              <tbody>
                {a.by_cabinet.map((row) => (
                  <tr key={row.name}>
                    <td>{row.name}</td>
                    <td>{row.count}</td>
                    <td>{formatBytes(row.bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted">
            Counts for the whole organization. Nothing here names a person, a document or a search.
          </p>
        </>
      ) : null}
    </section>
  );
}

function Jobs() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("");
  const summary = useQuery({
    queryKey: ["admin-job-summary"],
    queryFn: () => api.get<JobSummary>("/admin/jobs/summary"),
    refetchInterval: 15_000,
  });
  const jobs = useQuery({
    queryKey: ["admin-jobs", status],
    queryFn: () => api.get<AdminJob[]>("/admin/jobs", { query: status ? { status } : {} }),
    refetchInterval: 15_000,
  });
  const retry = useMutation({
    mutationFn: (id: string) => api.post(`/admin/jobs/${id}/retry`, {}),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-jobs"] });
      void queryClient.invalidateQueries({ queryKey: ["admin-job-summary"] });
      void queryClient.invalidateQueries({ queryKey: ["admin-health"] });
    },
  });
  const s = summary.data;
  return (
    <section>
      <h2>Background jobs</h2>
      <ErrorNotice error={summary.error ?? jobs.error ?? retry.error} />
      {s ? (
        <>
          <p>
            {Object.entries(s.counts)
              .map(([name, n]) => `${n} ${name}`)
              .join(", ")}
            . Longest wait: {waitText(s.queued_oldest_seconds)}.
          </p>
          {s.dead_types.length > 0 ? (
            <Notice tone="error">
              Jobs have given up:{" "}
              {s.dead_types.map((t) => `${t.count} of ${t.name}`).join(", ")}.
            </Notice>
          ) : null}
        </>
      ) : null}
      <label>
        Show{" "}
        <select value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">All</option>
          <option value="queued">Waiting</option>
          <option value="running">Running</option>
          <option value="failed">Failed</option>
          <option value="dead">Gave up</option>
          <option value="succeeded">Done</option>
        </select>
      </label>
      {jobs.data && jobs.data.length === 0 ? <p className="muted">No jobs.</p> : null}
      {jobs.data && jobs.data.length > 0 ? (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Background jobs, newest first</caption>
            <thead>
              <tr>
                <th scope="col">Job</th>
                <th scope="col">Status</th>
                <th scope="col" className="hide-narrow">
                  Tries
                </th>
                <th scope="col">Started</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {jobs.data.map((job) => (
                <tr key={job.id}>
                  <td>
                    {job.type}
                    {job.error ? <div className="muted">{job.error}</div> : null}
                  </td>
                  <td>{job.status}</td>
                  <td className="hide-narrow">
                    {job.attempts} of {job.max_attempts}
                  </td>
                  <td>{formatDate(job.created_at)}</td>
                  <td className="actions">
                    {job.status === "dead" || job.status === "failed" ? (
                      <button
                        type="button"
                        className="btn btn-quiet"
                        disabled={retry.isPending}
                        onClick={() => retry.mutate(job.id)}
                      >
                        Try again<span className="sr-only"> {job.type}</span>
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

export function Operations() {
  const { can } = useAuth();
  if (!can("admin.operate")) {
    return (
      <div>
        <h1>Operations</h1>
        <Notice tone="info">You do not have permission to see operations.</Notice>
      </div>
    );
  }
  return (
    <div>
      <h1>Operations</h1>
      <Health />
      <Usage />
      <Jobs />
    </div>
  );
}
