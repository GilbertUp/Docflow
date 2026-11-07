import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../../api/client";
import type { CabinetDetail, Children, Crumb, FolderDetail, Stats } from "../../api/types";
import {
  ActionMenu,
  EmptyState,
  ErrorNotice,
  Notice,
  Spinner,
  formatBytes,
  formatDate,
} from "../../components/ui";
import { AclDialog } from "./AclDialog";
import {
  DeleteDialog,
  DestinationDialog,
  NewFolderDialog,
  RenameDialog,
  type Target,
  useToggleArchive,
} from "./dialogs";

const PAGE_SIZE = 25;

type DialogState =
  | { kind: "new-folder" }
  | { kind: "rename"; target: Target }
  | { kind: "move"; target: Target }
  | { kind: "copy"; target: Target }
  | { kind: "delete"; target: Target }
  | { kind: "acl" }
  | null;

interface Container {
  type: "cabinet" | "folder";
  id: string;
  name: string;
  status: string;
  rowVersion: number;
  anchorId: string;
  cabinetId: string;
  parentId: string | null;
  permissions: string[];
  stats: Stats;
  crumbs: Crumb[];
}

function toContainer(kind: "cabinet" | "folder", data: CabinetDetail | FolderDetail): Container {
  if (kind === "folder") {
    const detail = data as FolderDetail;
    return {
      type: "folder",
      id: detail.folder.id,
      name: detail.folder.name,
      status: detail.folder.status,
      rowVersion: detail.folder.row_version,
      anchorId: detail.folder.acl_anchor_id,
      cabinetId: detail.folder.cabinet_id,
      parentId: detail.folder.parent_id ?? null,
      permissions: detail.permissions,
      stats: detail.stats,
      crumbs: detail.breadcrumb,
    };
  }
  const detail = data as CabinetDetail;
  return {
    type: "cabinet",
    id: detail.cabinet.id,
    name: detail.cabinet.name,
    status: detail.cabinet.status,
    rowVersion: detail.cabinet.row_version,
    anchorId: detail.cabinet.acl_anchor_id,
    cabinetId: detail.cabinet.id,
    parentId: null,
    permissions: detail.permissions,
    stats: detail.stats,
    crumbs: [{ id: detail.cabinet.id, name: detail.cabinet.name, kind: "cabinet" }],
  };
}

function crumbLink(crumb: Crumb): string | null {
  if (!crumb.id) return null;
  return `/files/${crumb.kind}s/${crumb.id}`;
}

export function Browser({ kind }: { kind: "cabinet" | "folder" }) {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [showArchived, setShowArchived] = useState(false);
  const [offset, setOffset] = useState(0);
  const [dialog, setDialog] = useState<DialogState>(null);
  const [message, setMessage] = useState<string | null>(null);
  const archive = useToggleArchive();

  const detail = useQuery({
    queryKey: ["detail", kind, id],
    queryFn: async (): Promise<CabinetDetail | FolderDetail> =>
      kind === "folder"
        ? await api.get<FolderDetail>(`/folders/${id}`)
        : await api.get<CabinetDetail>(`/cabinets/${id}`),
  });
  const children = useQuery({
    queryKey: ["children", kind, id, showArchived, offset],
    queryFn: () =>
      api.get<Children>(`/${kind}s/${id}/children`, {
        query: { include_archived: showArchived, limit: PAGE_SIZE, offset },
      }),
  });

  if (detail.isPending) return <Spinner label="Opening" />;
  if (detail.isError) {
    return (
      <div className="panel">
        <ErrorNotice error={detail.error} />
        <p>
          <Link to="/files">Back to all cabinets</Link>
        </p>
      </div>
    );
  }

  const container = toContainer(kind, detail.data);
  const can = (permission: string) => container.permissions.includes(permission);
  const self: Target = {
    type: container.type,
    id: container.id,
    name: container.name,
    rowVersion: container.rowVersion,
    status: container.status,
  };
  const archived = container.status === "archived";
  const folders = children.data?.folders ?? [];
  const documents = children.data?.documents;

  function leaveAfterDelete() {
    if (container.type === "cabinet") navigate("/files");
    else if (container.parentId) navigate(`/files/folders/${container.parentId}`);
    else navigate(`/files/cabinets/${container.cabinetId}`);
  }

  function folderTarget(folder: Children["folders"][number]): Target {
    return {
      type: "folder",
      id: folder.id,
      name: folder.name,
      rowVersion: folder.row_version,
      status: folder.status,
    };
  }

  return (
    <div>
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <ol>
          <li>
            <Link to="/files">All cabinets</Link>
          </li>
          {container.crumbs.map((crumb, index) => {
            const last = index === container.crumbs.length - 1;
            const link = crumbLink(crumb);
            return (
              <li key={`${crumb.id ?? "hidden"}-${index}`}>
                {last ? (
                  <span aria-current="page">{crumb.name}</span>
                ) : link ? (
                  <Link to={link}>{crumb.name}</Link>
                ) : (
                  <span className="muted" title="You do not have access to this level">
                    {crumb.name}
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      </nav>

      <h1>
        {container.name}
        {archived ? <span className="badge badge-warn">Archived</span> : null}
      </h1>

      <dl className="stats">
        <div>
          <dt>Folders</dt>
          <dd>{container.stats.subfolders}</dd>
        </div>
        <div>
          <dt>Documents here</dt>
          <dd>{container.stats.documents}</dd>
        </div>
        <div>
          <dt>Documents inside</dt>
          <dd>{container.stats.subtree_documents}</dd>
        </div>
        <div>
          <dt>Storage</dt>
          <dd>{formatBytes(container.stats.storage_bytes)}</dd>
        </div>
      </dl>

      <div className="toolbar" role="toolbar" aria-label="Actions for this location">
        <button
          type="button"
          className="btn btn-primary"
          disabled={!can("create") || archived}
          title={archived ? "Archived locations accept no new content" : undefined}
          onClick={() => setDialog({ kind: "new-folder" })}
        >
          New folder
        </button>
        <button
          type="button"
          className="btn"
          disabled={!can("edit_metadata")}
          onClick={() => setDialog({ kind: "rename", target: self })}
        >
          Rename
        </button>
        {container.type === "folder" ? (
          <>
            <button
              type="button"
              className="btn"
              disabled={!can("move")}
              onClick={() => setDialog({ kind: "move", target: self })}
            >
              Move…
            </button>
            <button type="button" className="btn" onClick={() => setDialog({ kind: "copy", target: self })}>
              Copy…
            </button>
          </>
        ) : null}
        <button
          type="button"
          className="btn"
          disabled={!can("delete") || archive.isPending}
          onClick={() => archive.mutate(self)}
        >
          {archived ? "Restore" : "Archive"}
        </button>
        <button
          type="button"
          className="btn"
          disabled={!can("manage_acl")}
          title={can("manage_acl") ? undefined : "You cannot change who has access here"}
          onClick={() => setDialog({ kind: "acl" })}
        >
          Permissions…
        </button>
        <button
          type="button"
          className="btn btn-danger"
          disabled={!can("delete")}
          onClick={() => setDialog({ kind: "delete", target: self })}
        >
          Delete
        </button>
        <span className="grow" />
        <label>
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(event) => {
              setShowArchived(event.target.checked);
              setOffset(0);
            }}
          />{" "}
          Show archived
        </label>
      </div>

      {message ? <Notice tone="success">{message}</Notice> : null}
      <ErrorNotice error={archive.error} />

      <section className="panel" aria-labelledby="folders-heading">
        <h2 id="folders-heading">Folders</h2>
        {children.isPending ? (
          <Spinner />
        ) : children.isError ? (
          <ErrorNotice error={children.error} />
        ) : folders.length === 0 ? (
          <EmptyState title="No folders here">
            {can("create") && !archived
              ? "Use “New folder” to organize this location."
              : "There is nothing you can see at this level."}
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table>
              <caption className="sr-only">Folders in {container.name}</caption>
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col" className="hide-narrow">
                    Created
                  </th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {folders.map((folder) => (
                  <tr key={folder.id}>
                    <td>
                      <Link to={`/files/folders/${folder.id}`}>{folder.name}</Link>
                      {folder.status === "archived" ? (
                        <span className="badge badge-warn">Archived</span>
                      ) : null}
                    </td>
                    <td className="hide-narrow">{formatDate(folder.created_at)}</td>
                    <td className="actions">
                      <ActionMenu label={`Actions for ${folder.name}`}>
                        <button type="button" onClick={() => setDialog({ kind: "rename", target: folderTarget(folder) })}>
                          Rename
                        </button>
                        <button type="button" onClick={() => setDialog({ kind: "move", target: folderTarget(folder) })}>
                          Move…
                        </button>
                        <button type="button" onClick={() => setDialog({ kind: "copy", target: folderTarget(folder) })}>
                          Copy…
                        </button>
                        <button type="button" onClick={() => archive.mutate(folderTarget(folder))}>
                          {folder.status === "archived" ? "Restore" : "Archive"}
                        </button>
                        <button
                          type="button"
                          className="danger"
                          onClick={() => setDialog({ kind: "delete", target: folderTarget(folder) })}
                        >
                          Delete
                        </button>
                      </ActionMenu>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel" aria-labelledby="documents-heading">
        <h2 id="documents-heading">Documents</h2>
        {documents && documents.total === 0 ? (
          <EmptyState title="No documents here">
            Uploading arrives with the next release. Documents filed by other means appear here.
          </EmptyState>
        ) : documents ? (
          <>
            <div className="table-wrap">
              <table>
                <caption className="sr-only">Documents in {container.name}</caption>
                <thead>
                  <tr>
                    <th scope="col">Title</th>
                    <th scope="col">Size</th>
                    <th scope="col">Filed</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {documents.items.map((document) => (
                    <tr key={document.id}>
                      <td>
                        {document.title}
                        {document.status === "archived" ? (
                          <span className="badge badge-warn">Archived</span>
                        ) : null}
                      </td>
                      <td>{formatBytes(document.size_bytes)}</td>
                      <td>{formatDate(document.created_at)}</td>
                      <td className="actions">
                        <button
                          type="button"
                          className="btn btn-quiet"
                          onClick={() =>
                            setDialog({
                              kind: "move",
                              target: {
                                type: "document",
                                id: document.id,
                                name: document.title,
                                rowVersion: document.row_version,
                              },
                            })
                          }
                        >
                          Move…
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="toolbar">
              <span className="muted grow">
                Showing {documents.offset + 1}–{documents.offset + documents.items.length} of{" "}
                {documents.total}
              </span>
              <button
                type="button"
                className="btn"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                Previous
              </button>
              <button
                type="button"
                className="btn"
                disabled={offset + PAGE_SIZE >= documents.total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
              </button>
            </div>
          </>
        ) : null}
      </section>

      {dialog?.kind === "new-folder" ? (
        <NewFolderDialog
          parent={{ type: container.type, id: container.id, name: container.name }}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog?.kind === "rename" ? (
        <RenameDialog target={dialog.target} onClose={() => setDialog(null)} />
      ) : null}
      {dialog?.kind === "move" || dialog?.kind === "copy" ? (
        <DestinationDialog
          mode={dialog.kind}
          target={dialog.target}
          onClose={() => setDialog(null)}
          onDone={setMessage}
        />
      ) : null}
      {dialog?.kind === "delete" ? (
        <DeleteDialog
          target={dialog.target}
          onClose={() => setDialog(null)}
          onDeleted={() => {
            if (dialog.target.id === container.id) leaveAfterDelete();
          }}
        />
      ) : null}
      {dialog?.kind === "acl" ? (
        <AclDialog anchorId={container.anchorId} title={container.name} onClose={() => setDialog(null)} />
      ) : null}
    </div>
  );
}
