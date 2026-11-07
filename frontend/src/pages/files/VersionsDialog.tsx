import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";

import { api } from "../../api/client";
import type { VersionResult, VersionRow } from "../../api/types";
import {
  Dialog,
  ErrorNotice,
  Form,
  FormActions,
  Notice,
  Spinner,
  TextField,
  formatBytes,
  formatDate,
} from "../../components/ui";
import { useRefreshFiles } from "./dialogs";

/** The history of one document, and a way to add a new version. */
export function VersionsDialog({
  documentId,
  title,
  canAdd,
  canDownload,
  onClose,
}: {
  documentId: string;
  title: string;
  canAdd: boolean;
  canDownload: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const refresh = useRefreshFiles();
  const fileInput = useRef<HTMLInputElement>(null);
  const fileId = useId();
  const [file, setFile] = useState<File | null>(null);
  const [reason, setReason] = useState("");
  const [added, setAdded] = useState<number | null>(null);

  const versions = useQuery({
    queryKey: ["versions", documentId],
    queryFn: () => api.get<VersionRow[]>(`/documents/${documentId}/versions`),
  });

  const add = useMutation({
    mutationFn: (chosen: File) =>
      api.upload<VersionResult>(`/documents/${documentId}/versions`, chosen, {
        reason: reason.trim(),
      }),
    onSuccess: (result) => {
      setAdded(result.version.number);
      setFile(null);
      setReason("");
      if (fileInput.current) fileInput.current.value = "";
      void queryClient.invalidateQueries({ queryKey: ["versions", documentId] });
      refresh();
    },
  });

  // Newest first, because that is what people look for.
  const rows = [...(versions.data ?? [])].reverse();
  const current = rows[0]?.number;

  return (
    <Dialog title={`Versions of ${title}`} onClose={onClose} wide>
      {versions.isPending ? <Spinner label="Loading versions" /> : null}
      <ErrorNotice error={versions.error} />
      {versions.data ? (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Versions of {title}</caption>
            <thead>
              <tr>
                <th scope="col">Version</th>
                <th scope="col">File</th>
                <th scope="col">Size</th>
                <th scope="col">Added</th>
                <th scope="col">Reason</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    {row.number}
                    {row.number === current ? <span className="badge badge-ok">Current</span> : null}
                  </td>
                  <td>{row.original_filename}</td>
                  <td>{formatBytes(row.size_bytes)}</td>
                  <td>{formatDate(row.created_at)}</td>
                  <td>{row.reason || "—"}</td>
                  <td className="actions">
                    {canDownload ? (
                      <a
                        className="btn btn-quiet"
                        href={`/api/v1/documents/${documentId}/versions/${row.number}/download`}
                        download
                      >
                        Download<span className="sr-only"> version {row.number}</span>
                      </a>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {canAdd ? (
        <Form
          onSubmit={() => {
            setAdded(null);
            if (file) add.mutate(file);
          }}
        >
          <h3>Add a new version</h3>
          <div className="field">
            <label htmlFor={fileId}>New version file</label>
            <input
              id={fileId}
              ref={fileInput}
              type="file"
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                setAdded(null);
              }}
            />
          </div>
          <TextField
            label="What changed (optional)"
            value={reason}
            onChange={setReason}
            maxLength={500}
          />
          <ErrorNotice error={add.error} />
          {added !== null ? <Notice tone="success">Version {added} was added.</Notice> : null}
          <FormActions>
            <button type="button" className="btn" onClick={onClose}>
              Done
            </button>
            <button type="submit" className="btn btn-primary" disabled={!file || add.isPending}>
              {add.isPending ? "Uploading…" : "Add version"}
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
