import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { api } from "../../api/client";
import type { ImportResult } from "../../api/types";
import { ErrorNotice, Notice } from "../../components/ui";
import { useRefreshFiles } from "./dialogs";

const WORKING = new Set(["queued", "running"]);

/** Follows one ZIP import until it ends, then lists what went wrong, if anything. */
export function ImportPanel({ id, onDismiss }: { id: string; onDismiss: () => void }) {
  const refresh = useRefreshFiles();
  const result = useQuery({
    queryKey: ["import", id],
    queryFn: () => api.get<ImportResult>(`/imports/${id}`),
    refetchInterval: (query) => (query.state.data && !WORKING.has(query.state.data.status) ? false : 1500),
  });
  const data = result.data;
  const working = !data || WORKING.has(data.status);

  // New folders and documents appear in the list as soon as the import ends.
  useEffect(() => {
    if (data && !WORKING.has(data.status)) refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.status]);

  if (result.isError) return <ErrorNotice error={result.error} />;
  const failures = data?.items.filter((item) => item.status === "failed") ?? [];

  return (
    <section className="panel" aria-labelledby={`import-${id}`}>
      <h2 id={`import-${id}`}>Importing {data?.archive_name ?? "archive"}</h2>
      {working ? (
        <p role="status">
          {data?.status === "running"
            ? `Filing files… ${data.filed + data.failed} of ${data.file_count} done.`
            : "Waiting to start…"}
        </p>
      ) : data?.status === "failed" ? (
        <Notice tone="error">The import could not finish. {data.error}</Notice>
      ) : (
        <Notice tone={failures.length > 0 ? "info" : "success"}>
          {data?.filed} of {data?.file_count} files were filed
          {data && data.duplicates > 0
            ? `, ${data.duplicates} of them already stored, so no extra space is used`
            : ""}
          {failures.length > 0 ? `. ${failures.length} could not be filed.` : "."}
        </Notice>
      )}
      {failures.length > 0 ? (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Files that were not filed</caption>
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col">Why not</th>
              </tr>
            </thead>
            <tbody>
              {failures.map((item) => (
                <tr key={item.path}>
                  <td>{item.path}</td>
                  <td>{item.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {!working ? (
        <div className="toolbar">
          <button type="button" className="btn" onClick={onDismiss}>
            Dismiss
          </button>
        </div>
      ) : null}
    </section>
  );
}
