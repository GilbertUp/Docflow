import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import type { SearchResult } from "../api/types";
import { EmptyState, ErrorNotice, Spinner } from "../components/ui";

const PAGE = 20;

export interface Piece {
  text: string;
  match: boolean;
}

/** Splits a snippet marked with [[double brackets]] into plain and matched pieces. */
export function splitSnippet(snippet: string): Piece[] {
  const pieces: Piece[] = [];
  const pattern = /\[\[(.*?)\]\]/gs;
  let last = 0;
  for (const found of snippet.matchAll(pattern)) {
    const start = found.index ?? 0;
    if (start > last) pieces.push({ text: snippet.slice(last, start), match: false });
    pieces.push({ text: found[1] ?? "", match: true });
    last = start + found[0].length;
  }
  if (last < snippet.length) pieces.push({ text: snippet.slice(last), match: false });
  return pieces;
}

/** Where a hit lives in the file browser. */
export function locationOf(hit: { cabinet_id: string; folder_id: string | null }): string {
  return hit.folder_id ? `/files/folders/${hit.folder_id}` : `/files/cabinets/${hit.cabinet_id}`;
}

const WHERE: Record<string, string> = {
  text: "Found in the text",
  both: "Found in the details and the text",
  details: "Found in the title or details",
};

export function Search() {
  const [params, setParams] = useSearchParams();
  const query = params.get("q") ?? "";
  const offset = Number(params.get("offset") ?? "0") || 0;
  const [draft, setDraft] = useState(query);

  const results = useQuery({
    queryKey: ["search", query, offset],
    enabled: query.trim().length > 0,
    queryFn: () =>
      api.get<SearchResult>("/search", { query: { q: query, limit: PAGE, offset } }),
    retry: false,
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const next = draft.trim();
    setParams(next ? { q: next } : {});
  }

  const data = results.data;
  return (
    <div>
      <h1>Search</h1>
      <form role="search" className="toolbar" onSubmit={submit}>
        <label className="sr-only" htmlFor="search-box">
          Search documents
        </label>
        <input
          id="search-box"
          type="search"
          value={draft}
          maxLength={200}
          placeholder="Words in a title, detail or the text itself"
          onChange={(event) => setDraft(event.target.value)}
          className="grow"
        />
        <button type="submit" className="btn btn-primary">
          Search
        </button>
      </form>
      <p className="muted">
        Use quotes for an exact phrase, “or” for either word, and a minus sign to leave a word out.
      </p>
      <ErrorNotice error={results.error} />
      {results.isFetching ? <Spinner label="Searching" /> : null}
      {data && data.total === 0 ? (
        <EmptyState title="Nothing found">
          Nothing you can read matches “{data.query}”. New files can take a moment to appear.
        </EmptyState>
      ) : null}
      {data && data.total > 0 ? (
        <>
          <p aria-live="polite">
            {data.total} {data.total === 1 ? "document" : "documents"}
            {data.truncated ? " (showing the best matches)" : ""}
          </p>
          <ol className="search-hits">
            {data.hits.map((hit) => (
              <li key={hit.id}>
                <Link to={locationOf(hit)}>{hit.title}</Link>
                {hit.type_name ? <span className="badge">{hit.type_name}</span> : null}
                <div className="muted">{WHERE[hit.matched_in] ?? ""}</div>
                {hit.snippet ? (
                  <p className="snippet">
                    {splitSnippet(hit.snippet).map((piece, index) =>
                      piece.match ? <mark key={index}>{piece.text}</mark> : piece.text,
                    )}
                  </p>
                ) : null}
              </li>
            ))}
          </ol>
          <div className="toolbar">
            <button
              type="button"
              className="btn"
              disabled={offset === 0}
              onClick={() => setParams({ q: query, offset: String(Math.max(0, offset - PAGE)) })}
            >
              Previous results
            </button>
            <button
              type="button"
              className="btn"
              disabled={offset + PAGE >= data.total}
              onClick={() => setParams({ q: query, offset: String(offset + PAGE) })}
            >
              More results
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}
