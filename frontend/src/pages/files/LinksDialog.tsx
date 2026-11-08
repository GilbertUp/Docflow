import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { DocumentLink, LinkType, SearchResult } from "../../api/types";
import {
  Dialog,
  ErrorNotice,
  Form,
  FormActions,
  Notice,
  SelectField,
  Spinner,
  TextField,
} from "../../components/ui";
import { locationOf } from "../Search";

/** Documents linked to this one, in both directions, and a way to link another. */
export function LinksDialog({
  documentId,
  title,
  canEdit,
  onClose,
}: {
  documentId: string;
  title: string;
  canEdit: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [find, setFind] = useState("");
  const [searched, setSearched] = useState("");
  const [kind, setKind] = useState("related");
  const [target, setTarget] = useState("");

  const links = useQuery({
    queryKey: ["links", documentId],
    queryFn: () => api.get<DocumentLink[]>(`/documents/${documentId}/links`),
  });
  const types = useQuery({
    queryKey: ["link-types"],
    queryFn: () => api.get<LinkType[]>("/document-link-types"),
  });
  const found = useQuery({
    queryKey: ["link-search", searched],
    enabled: searched.length > 0,
    queryFn: () => api.get<SearchResult>("/search", { query: { q: searched, limit: 8 } }),
    retry: false,
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["links", documentId] });
  const add = useMutation({
    mutationFn: () =>
      api.post(`/documents/${documentId}/links`, { to_document_id: target, relation_type: kind }),
    onSuccess: () => {
      setTarget("");
      refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/documents/${documentId}/links/${id}`),
    onSuccess: refresh,
  });

  const candidates = (found.data?.hits ?? []).filter((hit) => hit.id !== documentId);
  const wording = types.data?.find((item) => item.type === kind)?.from_wording ?? "Related to";

  return (
    <Dialog title={`Links of ${title}`} onClose={onClose} wide>
      {links.isPending ? <Spinner label="Loading links" /> : null}
      <ErrorNotice error={links.error ?? remove.error} />
      {links.data && links.data.length === 0 ? (
        <Notice tone="info">This document is not linked to any other you can read.</Notice>
      ) : null}
      {links.data && links.data.length > 0 ? (
        <ul className="link-list">
          {links.data.map((item) => (
            <li key={item.id}>
              {item.wording}{" "}
              <Link to={locationOf(item.document)} onClick={onClose}>
                {item.document.title}
              </Link>
              {item.can_remove && canEdit ? (
                <button
                  type="button"
                  className="btn btn-quiet"
                  disabled={remove.isPending}
                  onClick={() => remove.mutate(item.id)}
                >
                  Remove<span className="sr-only"> link to {item.document.title}</span>
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {canEdit ? (
        <Form
          onSubmit={() => {
            if (target) add.mutate();
            else setSearched(find.trim());
          }}
        >
          <h3>Link another document</h3>
          <SelectField
            label="This document"
            value={kind}
            onChange={setKind}
            options={(types.data ?? []).map((item) => ({
              value: item.type,
              label: item.from_wording,
            }))}
          />
          <TextField
            label="Find the other document"
            value={find}
            onChange={(value) => {
              setFind(value);
              setTarget("");
            }}
            hint="Words from its title, details or text. Press Enter to search."
          />
          {searched && found.data && candidates.length === 0 ? (
            <p className="muted">No other document matches.</p>
          ) : null}
          {candidates.length > 0 ? (
            <SelectField
              label="Choose it"
              value={target}
              onChange={setTarget}
              options={candidates.map((hit) => ({ value: hit.id, label: hit.title }))}
              placeholder="Choose a document"
            />
          ) : null}
          <ErrorNotice error={add.error} />
          <FormActions>
            <button type="button" className="btn" onClick={onClose}>
              Done
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={add.isPending || (!target && !find.trim())}
            >
              {target ? `Link as “${wording}”` : "Find"}
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
