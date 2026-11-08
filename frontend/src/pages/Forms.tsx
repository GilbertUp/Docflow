import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, fieldErrors } from "../api/client";
import type { FormRow, SubmissionRow } from "../api/types";
import { EmptyState, ErrorNotice, Form, FormActions, Notice, Spinner, formatDate } from "../components/ui";
import { FieldInput } from "./files/DetailsDialog";

/** The answers to send: every field the person filled in, as text. */
export function answersOf(values: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value.trim() !== ""));
}

/** The starting answers, from each field's default. */
export function startingValues(fields: FormRow["fields"]): Record<string, string> {
  return Object.fromEntries(fields.map((field) => [field.key, field.default_value ?? ""]));
}

export function Forms() {
  const forms = useQuery({ queryKey: ["forms"], queryFn: () => api.get<FormRow[]>("/forms") });
  return (
    <div>
      <h1>Forms</h1>
      <ErrorNotice error={forms.error} />
      {forms.isPending ? <Spinner label="Loading forms" /> : null}
      {forms.data && forms.data.length === 0 ? (
        <EmptyState title="No forms">Nobody has made a form yet.</EmptyState>
      ) : null}
      <ul className="form-list">
        {(forms.data ?? []).map((form) => (
          <li key={form.id}>
            <Link to={`/forms/${form.id}`}>{form.name}</Link>
            {form.description ? <div className="muted">{form.description}</div> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function FillForm() {
  const { id = "" } = useParams();
  const queryClient = useQueryClient();
  const form = useQuery({ queryKey: ["form", id], queryFn: () => api.get<FormRow>(`/forms/${id}`) });
  const mine = useQuery({
    queryKey: ["form-submissions", id],
    queryFn: () => api.get<SubmissionRow[]>(`/forms/${id}/submissions`),
  });
  const [values, setValues] = useState<Record<string, string> | null>(null);
  const [sent, setSent] = useState(false);
  const shown = values ?? (form.data ? startingValues(form.data.fields) : {});

  const submit = useMutation({
    mutationFn: () => api.post(`/forms/${id}/submissions`, { answers: answersOf(shown) }),
    onSuccess: () => {
      setSent(true);
      setValues(form.data ? startingValues(form.data.fields) : {});
      void queryClient.invalidateQueries({ queryKey: ["form-submissions", id] });
    },
  });
  const errors = fieldErrors(submit.error);

  return (
    <div>
      <p>
        <Link to="/forms">All forms</Link>
      </p>
      {form.isPending ? <Spinner label="Loading the form" /> : null}
      <ErrorNotice error={form.error} />
      {form.data ? (
        <>
          <h1>{form.data.name}</h1>
          {form.data.description ? <p className="muted">{form.data.description}</p> : null}
          {form.data.status === "archived" ? (
            <Notice tone="info">This form is archived and cannot be filled in.</Notice>
          ) : (
            <Form
              onSubmit={() => {
                setSent(false);
                submit.mutate();
              }}
            >
              {form.data.fields.map((field) => (
                <FieldInput
                  key={field.key}
                  field={{ ...field, read_only: false }}
                  value={shown[field.key] ?? ""}
                  error={errors[field.key]}
                  disabled={false}
                  onChange={(value) => {
                    setSent(false);
                    setValues({ ...shown, [field.key]: value });
                  }}
                />
              ))}
              <ErrorNotice error={submit.error && Object.keys(errors).length === 0 ? submit.error : null} />
              {Object.keys(errors).length > 0 ? (
                <Notice tone="error">Some answers are not valid. They are marked above.</Notice>
              ) : null}
              {sent ? <Notice tone="success">Submitted. Thank you.</Notice> : null}
              <FormActions>
                <button type="submit" className="btn btn-primary" disabled={submit.isPending}>
                  {submit.isPending ? "Sending…" : "Submit"}
                </button>
              </FormActions>
            </Form>
          )}
          <h2>Submitted</h2>
          {mine.data && mine.data.length === 0 ? <p className="muted">Nothing submitted yet.</p> : null}
          <ul className="form-list">
            {(mine.data ?? []).map((item) => (
              <li key={item.id}>
                <strong>{formatDate(item.created_at)}</strong> by {item.submitter_name}
                <dl className="answers">
                  {Object.entries(item.data).map(([key, value]) => (
                    <div key={key}>
                      <dt>{item.labels[key] ?? key}</dt>
                      <dd>{value === null || value === "" ? "—" : String(value)}</dd>
                    </div>
                  ))}
                </dl>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}
