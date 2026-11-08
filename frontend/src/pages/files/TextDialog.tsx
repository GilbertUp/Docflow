import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../../api/client";
import type { LanguageList, TextResult } from "../../api/types";
import { Dialog, ErrorNotice, Notice, Spinner } from "../../components/ui";

const WORKING = new Set(["pending", "running"]);
const SPECIAL_NAMES: Record<string, string> = {
  chi_sim: "Chinese (Simplified)",
  chi_tra: "Chinese (Traditional)",
  srp_latn: "Serbian (Latin)",
  uzb_cyrl: "Uzbek (Cyrillic)",
  aze_cyrl: "Azerbaijani (Cyrillic)",
  frk: "German (Fraktur)",
  enm: "English (Middle)",
  grc: "Greek (Ancient)",
};

/** A readable name for a Tesseract language code, with the code kept as a fallback. */
export function languageName(code: string): string {
  const special = SPECIAL_NAMES[code];
  if (special) return special;
  try {
    const name = new Intl.DisplayNames(["en"], { type: "language" }).of(code);
    if (name && name.toLowerCase() !== code.toLowerCase()) return name;
  } catch {
    // An unknown code falls through to the code itself.
  }
  return code;
}

export function stateLabel(state: string): string {
  switch (state) {
    case "pending":
      return "Waiting to be read";
    case "running":
      return "Being read";
    case "done":
      return "Text ready";
    case "failed":
      return "Could not be read";
    case "unsupported":
      return "No text reading for this file type yet";
    default:
      return "Not read yet";
  }
}

/** The text of a document, page by page, and a way to read it again in other languages. */
export function TextDialog({
  documentId,
  title,
  canReprocess,
  onClose,
}: {
  documentId: string;
  title: string;
  canReprocess: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [chosen, setChosen] = useState<string[]>([]);
  const [filter, setFilter] = useState("");

  const text = useQuery({
    queryKey: ["text", documentId],
    queryFn: () => api.get<TextResult>(`/documents/${documentId}/text`),
    refetchInterval: (query) =>
      query.state.data && !WORKING.has(query.state.data.state) ? false : 1500,
  });
  const languages = useQuery({
    queryKey: ["ocr-languages"],
    queryFn: () => api.get<LanguageList>("/ocr/languages"),
    enabled: canReprocess,
  });

  const again = useMutation({
    mutationFn: () =>
      api.post(`/documents/${documentId}/text/reprocess`, undefined, {
        query: { languages: chosen.join(",") },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["text", documentId] });
      void queryClient.invalidateQueries({ queryKey: ["versions", documentId] });
    },
  });

  const installed = languages.data?.languages;
  const max = languages.data?.max_per_run ?? 5;
  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return (installed ?? [])
      .map((code) => ({ code, name: languageName(code) }))
      .filter(
        (item) => !needle || item.code.includes(needle) || item.name.toLowerCase().includes(needle),
      )
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [installed, filter]);

  function toggle(code: string) {
    setChosen((current) =>
      current.includes(code) ? current.filter((item) => item !== code) : [...current, code],
    );
  }

  const data = text.data;
  const working = !data || WORKING.has(data.state);

  return (
    <Dialog title={`Text of ${title}`} onClose={onClose} wide>
      <ErrorNotice error={text.error} />
      {text.isPending ? <Spinner label="Loading the text" /> : null}
      {data ? (
        <>
          <p role="status" className="muted">
            Version {data.version}: {stateLabel(data.state)}
            {data.total_pages > 0
              ? ` · ${data.total_pages} page${data.total_pages === 1 ? "" : "s"}`
              : ""}
          </p>
          {data.state === "failed" && data.error ? (
            <Notice tone="error">{data.error}</Notice>
          ) : null}
          <div className="text-pages">
            {data.pages.map((page) => (
              <section key={page.number} aria-label={`Page ${page.number}`}>
                <h3>
                  Page {page.number}
                  {page.is_blank ? (
                    <span className="badge">Blank</span>
                  ) : page.source === "native" ? (
                    <span className="badge badge-ok">From the file</span>
                  ) : (
                    <span className="badge badge-warn">
                      Recognised
                      {page.language
                        ? ` in ${page.language.split("+").map(languageName).join(", ")}`
                        : ""}
                      {page.confidence !== null
                        ? `, ${Math.round(page.confidence * 100)}% sure`
                        : ""}
                    </span>
                  )}
                </h3>
                {page.is_blank ? null : (
                  <pre className="text-page">{page.text || "(no text found)"}</pre>
                )}
              </section>
            ))}
            {!working && data.pages.length === 0 && data.state === "done" ? (
              <p className="muted">No text was found in this file.</p>
            ) : null}
          </div>
        </>
      ) : null}

      {canReprocess ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            again.mutate();
          }}
        >
          <fieldset className="languages">
            <legend>Read again in other languages</legend>
            {languages.data && !languages.data.available ? (
              <Notice tone="info">
                Reading scanned pages is not set up on this server, so only files that already
                contain text can be read.
              </Notice>
            ) : (
              <>
                <p className="hint">
                  Choose up to {max}. Several languages at once is slower and a little less
                  accurate.
                  {chosen.length > 0 ? ` Chosen: ${chosen.map(languageName).join(", ")}.` : ""}
                </p>
                <div className="field">
                  <label htmlFor="language-filter">Find a language</label>
                  <input
                    id="language-filter"
                    type="text"
                    value={filter}
                    onChange={(event) => setFilter(event.target.value)}
                  />
                </div>
                <div className="language-list" role="group" aria-label="Languages">
                  {shown.map((item) => (
                    <label key={item.code} className="check">
                      <input
                        type="checkbox"
                        checked={chosen.includes(item.code)}
                        disabled={!chosen.includes(item.code) && chosen.length >= max}
                        onChange={() => toggle(item.code)}
                      />
                      {item.name} <span className="muted">({item.code})</span>
                    </label>
                  ))}
                </div>
              </>
            )}
          </fieldset>
          <ErrorNotice error={again.error} />
          {again.isSuccess ? (
            <Notice tone="success">Reading again. The text updates when it is done.</Notice>
          ) : null}
          <div className="form-actions">
            <button type="button" className="btn" onClick={onClose}>
              Done
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={
                again.isPending || working || chosen.length === 0 || !languages.data?.available
              }
            >
              {again.isPending ? "Starting…" : "Read again"}
            </button>
          </div>
        </form>
      ) : (
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>
            Done
          </button>
        </div>
      )}
    </Dialog>
  );
}
