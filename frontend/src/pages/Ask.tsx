import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { AssistantAnswer, AssistantSettings, Cabinet } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ErrorNotice, Form, FormActions, Notice, Spinner } from "../components/ui";

/** What kind of answer this is, in words that cannot be mistaken for something else. */
export function modeLabel(mode: string): string {
  switch (mode) {
    case "generated":
      return "Written by an AI service from the documents below";
    case "extracts":
      return "Extracts from your documents. Nothing has been written or summarised";
    default:
      return "Not enough evidence";
  }
}

/** Where a source lives in the file browser. */
export function sourceLocation(source: { cabinet_id: string; folder_id: string | null }): string {
  return source.folder_id ? `/files/folders/${source.folder_id}` : `/files/cabinets/${source.cabinet_id}`;
}

function Controls() {
  const queryClient = useQueryClient();
  const settings = useQuery({
    queryKey: ["assistant-settings"],
    queryFn: () => api.get<AssistantSettings>("/assistant/settings"),
  });
  const cabinets = useQuery({
    queryKey: ["cabinets", "all"],
    queryFn: () => api.get<{ items: Cabinet[] }>("/cabinets"),
  });
  const save = useMutation({
    mutationFn: (input: { enabled: boolean; excluded: string[] }) =>
      api.put("/assistant/settings", {
        generation_enabled: input.enabled,
        excluded_cabinet_ids: input.excluded,
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["assistant-settings"] }),
  });
  const data = settings.data;
  if (!data) return null;
  const toggleCabinet = (id: string, on: boolean) =>
    data.excluded_cabinet_ids.filter((x) => x !== id).concat(on ? [id] : []);

  return (
    <section>
      <h2>Settings</h2>
      <ErrorNotice error={save.error} />
      {!data.provider_configured ? (
        <Notice tone="info">
          No AI service is set up on this server, so the assistant can only return extracts.
        </Notice>
      ) : (
        <>
          <label className="check">
            <input
              type="checkbox"
              checked={data.generation_enabled}
              disabled={save.isPending}
              onChange={(event) =>
                save.mutate({ enabled: event.target.checked, excluded: data.excluded_cabinet_ids })
              }
            />
            Let the AI service write answers ({data.provider}, {data.model})
          </label>
          <p className="muted">
            Passages are sent to that service to write an answer. Leave cabinets out below if their
            text must never be sent.
          </p>
          <fieldset>
            <legend>Never send text from these cabinets</legend>
            {(cabinets.data?.items ?? []).map((cabinet) => (
              <label className="check" key={cabinet.id}>
                <input
                  type="checkbox"
                  checked={data.excluded_cabinet_ids.includes(cabinet.id)}
                  disabled={save.isPending}
                  onChange={(event) =>
                    save.mutate({
                      enabled: data.generation_enabled,
                      excluded: toggleCabinet(cabinet.id, event.target.checked),
                    })
                  }
                />
                {cabinet.name}
              </label>
            ))}
          </fieldset>
        </>
      )}
    </section>
  );
}

export function Ask() {
  const { can } = useAuth();
  const [question, setQuestion] = useState("");
  const ask = useMutation({
    mutationFn: () => api.post<AssistantAnswer>("/assistant/ask", { question: question.trim() }),
  });
  const answer = ask.data;

  return (
    <div>
      <h1>Ask your documents</h1>
      <p className="muted">
        Answers come only from documents you can read, and always show where they came from.
      </p>
      <Form onSubmit={() => ask.mutate()}>
        <div className="field">
          <label htmlFor="question">Your question</label>
          <textarea
            id="question"
            rows={3}
            maxLength={500}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
          />
          <p className="hint">Use specific words, such as a name, a number or a date.</p>
        </div>
        <ErrorNotice error={ask.error} />
        <FormActions>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={ask.isPending || !question.trim()}
          >
            {ask.isPending ? "Looking…" : "Ask"}
          </button>
        </FormActions>
      </Form>
      {ask.isPending ? <Spinner label="Looking through your documents" /> : null}
      {answer ? (
        <section aria-live="polite" className="answer">
          <p>
            <span className="badge">{modeLabel(answer.mode)}</span>
          </p>
          {answer.answer ? <p className="answer-text">{answer.answer}</p> : null}
          {answer.notices.map((notice) => (
            <Notice key={notice} tone={answer.mode === "insufficient_evidence" ? "info" : "info"}>
              {notice}
            </Notice>
          ))}
          {answer.flagged ? (
            <Notice tone="error">
              Part of this answer could not be matched to a source. Check the sources before relying
              on it.
            </Notice>
          ) : null}
          {answer.sources.length > 0 ? (
            <>
              <h2>{answer.mode === "generated" ? "Sources" : "Passages"}</h2>
              <ol className="sources">
                {answer.sources.map((source) => (
                  <li key={source.number} value={source.number}>
                    <Link to={sourceLocation(source)}>{source.title}</Link>, page {source.page}
                    <blockquote>{source.snippet}</blockquote>
                  </li>
                ))}
              </ol>
            </>
          ) : null}
        </section>
      ) : null}
      {can("integrations.manage") ? <Controls /> : null}
    </div>
  );
}
