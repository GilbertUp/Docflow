import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Outlet } from "react-router-dom";

import { api } from "../../api/client";
import type { Cabinet, Page } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { FolderTree } from "../../components/FolderTree";
import { EmptyState, ErrorNotice, Spinner, formatDate } from "../../components/ui";
import { NewCabinetDialog } from "./dialogs";

/** Tree on the left, whatever the route shows on the right. */
export function FilesLayout() {
  return (
    <div className="split">
      <aside className="panel" aria-label="Browse">
        <h2>Browse</h2>
        <FolderTree mode="navigate" />
      </aside>
      <section aria-label="Contents">
        <Outlet />
      </section>
    </div>
  );
}

export function CabinetsOverview() {
  const { can } = useAuth();
  const [creating, setCreating] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const cabinets = useQuery({
    queryKey: ["cabinets", showArchived],
    queryFn: () =>
      api.get<Page<Cabinet>>("/cabinets", { query: { limit: 200, include_archived: showArchived } }),
  });

  return (
    <div>
      <h1>Cabinets</h1>
      <div className="toolbar">
        {can("cabinets.manage") ? (
          <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
            New cabinet
          </button>
        ) : null}
        <span className="grow" />
        <label>
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(event) => setShowArchived(event.target.checked)}
          />{" "}
          Show archived
        </label>
      </div>
      {cabinets.isPending ? (
        <Spinner />
      ) : cabinets.isError ? (
        <ErrorNotice error={cabinets.error} />
      ) : cabinets.data.items.length === 0 ? (
        <div className="panel">
          <EmptyState title="No cabinets you can see">
            {can("cabinets.manage")
              ? "Create the first cabinet, such as Finance or HR."
              : "Ask an administrator to give you access to a cabinet."}
          </EmptyState>
        </div>
      ) : (
        <div className="panel table-wrap">
          <table>
            <caption className="sr-only">Cabinets you can access</caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Description</th>
                <th scope="col">Created</th>
              </tr>
            </thead>
            <tbody>
              {cabinets.data.items.map((cabinet) => (
                <tr key={cabinet.id}>
                  <td>
                    <Link to={`/files/cabinets/${cabinet.id}`}>{cabinet.name}</Link>
                    {cabinet.status === "archived" ? (
                      <span className="badge badge-warn">Archived</span>
                    ) : null}
                  </td>
                  <td className="muted">{cabinet.description || "—"}</td>
                  <td>{formatDate(cabinet.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {creating ? <NewCabinetDialog onClose={() => setCreating(false)} /> : null}
    </div>
  );
}
