import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { NavLink } from "react-router-dom";

import { api } from "../api/client";
import type { Children, Cabinet, Page } from "../api/types";
import { ErrorNotice, Spinner } from "./ui";

export type NodeKind = "cabinet" | "folder";

export interface Pick {
  kind: NodeKind;
  id: string;
  name: string;
}

interface TreeProps {
  /** Navigate mode links to the item. Pick mode lets a dialog choose a destination. */
  mode: "navigate" | "pick";
  selected?: Pick | null;
  onPick?: (pick: Pick) => void;
  /** Items that cannot be chosen, such as the folder being moved. */
  disabledIds?: ReadonlySet<string>;
}

export function FolderTree({ mode, selected = null, onPick, disabledIds }: TreeProps) {
  const cabinets = useQuery({
    queryKey: ["tree", "cabinets"],
    queryFn: () => api.get<Page<Cabinet>>("/cabinets", { query: { limit: 200 } }),
  });

  if (cabinets.isPending) return <Spinner label="Loading cabinets" />;
  if (cabinets.isError) return <ErrorNotice error={cabinets.error} />;
  if (cabinets.data.items.length === 0) {
    return <p className="muted">No cabinets yet.</p>;
  }
  return (
    <ul className="tree" aria-label="Cabinets and folders">
      {cabinets.data.items.map((cabinet) => (
        <TreeNode
          key={cabinet.id}
          kind="cabinet"
          id={cabinet.id}
          name={cabinet.name}
          archived={cabinet.status === "archived"}
          mode={mode}
          selected={selected}
          onPick={onPick}
          disabledIds={disabledIds}
        />
      ))}
    </ul>
  );
}

interface NodeProps extends TreeProps {
  kind: NodeKind;
  id: string;
  name: string;
  archived: boolean;
}

function TreeNode({ kind, id, name, archived, mode, selected, onPick, disabledIds }: NodeProps) {
  const [open, setOpen] = useState(false);
  const children = useQuery({
    queryKey: ["tree", kind, id],
    queryFn: () =>
      api.get<Children>(`/${kind}s/${id}/children`, { query: { limit: 1, include_archived: false } }),
    enabled: open,
  });
  const disabled = disabledIds?.has(id) ?? false;
  const label = archived ? `${name} (archived)` : name;

  return (
    <li>
      <div className="tree-row">
        <button
          type="button"
          className="tree-toggle"
          aria-expanded={open}
          aria-label={`${open ? "Collapse" : "Expand"} ${name}`}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? "▾" : "▸"}
        </button>
        {mode === "navigate" ? (
          <NavLink className="tree-link" to={`/files/${kind}s/${id}`} title={label}>
            {label}
          </NavLink>
        ) : (
          <button
            type="button"
            className="tree-link"
            aria-pressed={selected?.id === id}
            disabled={disabled}
            title={disabled ? "You cannot move something into itself" : label}
            onClick={() => onPick?.({ kind, id, name })}
          >
            {label}
          </button>
        )}
      </div>
      {open ? (
        children.isPending ? (
          <Spinner label="Loading" />
        ) : children.isError ? (
          <ErrorNotice error={children.error} />
        ) : children.data.folders.length === 0 ? (
          <p className="muted" style={{ marginLeft: "2rem" }}>
            No folders
          </p>
        ) : (
          <ul>
            {children.data.folders.map((folder) => (
              <TreeNode
                key={folder.id}
                kind="folder"
                id={folder.id}
                name={folder.name}
                archived={folder.status === "archived"}
                mode={mode}
                selected={selected}
                onPick={onPick}
                // Everything under a disabled item is disabled too, because it sits inside it.
                disabledIds={disabled ? new Set([...(disabledIds ?? []), folder.id]) : disabledIds}
              />
            ))}
          </ul>
        )
      ) : null}
    </li>
  );
}
