import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, fieldErrors } from "../../api/client";
import type { CopyResult, Destination } from "../../api/types";
import { FolderTree, type Pick } from "../../components/FolderTree";
import {
  ConfirmDialog,
  Dialog,
  ErrorNotice,
  Form,
  FormActions,
  Notice,
  TextField,
} from "../../components/ui";

/** What an action applies to. Row versions let the server refuse edits made on stale data. */
export interface Target {
  type: "cabinet" | "folder" | "document";
  id: string;
  name: string;
  rowVersion: number;
  status?: string;
}

export function useRefreshFiles() {
  const queryClient = useQueryClient();
  return () => {
    for (const key of ["tree", "children", "detail", "cabinets"]) {
      void queryClient.invalidateQueries({ queryKey: [key] });
    }
  };
}

function path(target: Target): string {
  return `/${target.type}s/${target.id}`;
}

export function NewFolderDialog({
  parent,
  onClose,
}: {
  parent: { type: "cabinet" | "folder"; id: string; name: string };
  onClose: () => void;
}) {
  const refresh = useRefreshFiles();
  const [name, setName] = useState("");
  const save = useMutation({
    mutationFn: () => api.post(`/${parent.type}s/${parent.id}/folders`, { name: name.trim() }),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  return (
    <Dialog title={`New folder in ${parent.name}`} onClose={onClose}>
      <Form onSubmit={() => save.mutate()}>
        <TextField
          label="Folder name"
          value={name}
          onChange={setName}
          autoFocus
          required
          hint="Names must be unique in this place, ignoring upper and lower case."
          error={fieldErrors(save.error)["name"]}
        />
        <ErrorNotice error={save.error} />
        <FormActions>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={save.isPending || !name.trim()}>
            {save.isPending ? "Creating…" : "Create folder"}
          </button>
        </FormActions>
      </Form>
    </Dialog>
  );
}

export function NewCabinetDialog({ onClose }: { onClose: () => void }) {
  const refresh = useRefreshFiles();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const save = useMutation({
    mutationFn: () => api.post("/cabinets", { name: name.trim(), description: description.trim() }),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  return (
    <Dialog title="New cabinet" onClose={onClose}>
      <Form onSubmit={() => save.mutate()}>
        <TextField label="Cabinet name" value={name} onChange={setName} autoFocus required />
        <TextField label="Description" value={description} onChange={setDescription} />
        <Notice tone="info">You become the owner and can then choose who else has access.</Notice>
        <ErrorNotice error={save.error} />
        <FormActions>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={save.isPending || !name.trim()}>
            {save.isPending ? "Creating…" : "Create cabinet"}
          </button>
        </FormActions>
      </Form>
    </Dialog>
  );
}

export function RenameDialog({ target, onClose }: { target: Target; onClose: () => void }) {
  const refresh = useRefreshFiles();
  const [name, setName] = useState(target.name);
  const save = useMutation({
    mutationFn: () => api.patch(path(target), { row_version: target.rowVersion, name: name.trim() }),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  return (
    <Dialog title={`Rename ${target.type}`} onClose={onClose}>
      <Form onSubmit={() => save.mutate()}>
        <TextField label="Name" value={name} onChange={setName} autoFocus required />
        <ErrorNotice error={save.error} />
        <FormActions>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={save.isPending || !name.trim() || name.trim() === target.name}
          >
            {save.isPending ? "Saving…" : "Rename"}
          </button>
        </FormActions>
      </Form>
    </Dialog>
  );
}

function toDestination(pick: Pick): Destination {
  return pick.kind === "cabinet" ? { cabinet_id: pick.id } : { folder_id: pick.id };
}

export function DestinationDialog({
  mode,
  target,
  onClose,
  onDone,
}: {
  mode: "move" | "copy";
  target: Target;
  onClose: () => void;
  onDone?: (message: string) => void;
}) {
  const refresh = useRefreshFiles();
  const [picked, setPicked] = useState<Pick | null>(null);
  const save = useMutation({
    mutationFn: async () => {
      const destination = toDestination(picked as Pick);
      if (mode === "copy") {
        return api.post<CopyResult>(`${path(target)}/copy`, { destination });
      }
      return api.post(`${path(target)}/move`, { destination, row_version: target.rowVersion });
    },
    onSuccess: (result) => {
      refresh();
      if (mode === "copy" && result) {
        const copy = result as CopyResult;
        const skipped = copy.skipped ? ` ${copy.skipped} skipped because you cannot read them.` : "";
        onDone?.(`Copied ${copy.copied} folder${copy.copied === 1 ? "" : "s"}.${skipped}`);
      } else {
        onDone?.(`Moved “${target.name}”.`);
      }
      onClose();
    },
  });
  const title = `${mode === "move" ? "Move" : "Copy"} “${target.name}”`;
  return (
    <Dialog title={title} onClose={onClose} wide>
      <p className="muted">Choose where it should go.</p>
      <div className="panel" style={{ maxHeight: "18rem", overflow: "auto" }}>
        <FolderTree
          mode="pick"
          selected={picked}
          onPick={setPicked}
          disabledIds={target.type === "folder" ? new Set([target.id]) : undefined}
        />
      </div>
      {mode === "copy" ? (
        <Notice tone="info">Copying duplicates the folder structure, not documents or permissions.</Notice>
      ) : null}
      <ErrorNotice error={save.error} />
      <FormActions>
        <button type="button" className="btn" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={!picked || save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "Working…" : picked ? `${mode === "move" ? "Move" : "Copy"} to ${picked.name}` : "Choose a destination"}
        </button>
      </FormActions>
    </Dialog>
  );
}

export function DeleteDialog({
  target,
  onClose,
  onDeleted,
}: {
  target: Target;
  onClose: () => void;
  onDeleted?: () => void;
}) {
  const refresh = useRefreshFiles();
  const remove = useMutation({
    mutationFn: () => api.delete(path(target)),
    onSuccess: () => {
      refresh();
      onDeleted?.();
      onClose();
    },
  });
  return (
    <ConfirmDialog
      title={`Delete ${target.type}`}
      message={
        <>
          Delete <strong>{target.name}</strong>? Only empty {target.type}s can be deleted, and this
          cannot be undone. You can archive it instead to hide it.
        </>
      }
      confirmLabel="Delete"
      danger
      pending={remove.isPending}
      error={remove.error}
      onConfirm={() => remove.mutate()}
      onClose={onClose}
    />
  );
}

export function useToggleArchive() {
  const refresh = useRefreshFiles();
  return useMutation({
    mutationFn: (target: Target) =>
      api.patch(path(target), {
        row_version: target.rowVersion,
        status: target.status === "archived" ? "active" : "archived",
      }),
    onSuccess: refresh,
  });
}
