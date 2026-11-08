# ADR 0033: Cited answers from permitted passages, with extracts as the default

**Status:** Accepted on 2026-11-08 (F18, first slice)

## Context
[ADR 0013](0013-ai-assistant-and-providers.md) sets the rules: permission safety comes from retrieval, not from model behaviour, answers cite their sources, no evidence means no answer, and with no model the assistant returns labelled extracts. This records what the first slice builds and where it stops short.

## Decision
- **Passages.** The search job (ADR 0023) also cuts a document's current text into passages of up to 1,000 characters, never across a page, with overlap when a page is long, and stores them in `search_chunk` with their page number. They are rebuilt with the document's search row and removed with the document, including at disposal.
- **Retrieval is the permission filter.** A question is reduced to its meaningful words (English and French stopwords are dropped). Passages are found by full-text search with any word matching, then kept only if the person may `read` the document and may see its text (`preview`), and only if at least half of the question's words appear in the passage. The best six, ordered by how many of the words they cover, are the evidence. A model sees none of this until it has been filtered, so it cannot be made to reveal what the person could not open.
- **No evidence, no answer.** With no passage that qualifies the assistant says so. The model is not called, and no passages are shown.
- **Three answer modes, never confused.** `extracts` (the passages themselves, labelled as extracts), `generated` (written by an AI service, with its sources), and `insufficient_evidence`. The page says in words which one it is, and extracts are never presented as a written answer.
- **Generated answers start off.** They need a provider on the server (`DOCUFLOW_AI_PROVIDER` as `openai_compatible` for local or cloud servers that speak that protocol, or `anthropic`, with a model and key) and an administrator with `integrations.manage` to switch them on for the organization. Turning them on with no provider is refused, so a setting never looks active when it is not. An organization can exclude cabinets: their text is never sent to the provider, and if all the evidence is in them the person gets extracts and is told why. Keys live in the server's settings, never in the database.
- **The model gets no tools,** no way to act, and passages fenced as untrusted data that it is told to ignore as instructions. A passage cannot close its own fence. The provider's request is built in one place and a test confirms it carries no tool or function fields.
- **Answers are checked afterwards.** Every citation must name a passage that was supplied. A sentence with a missing or invalid citation is removed and the answer is flagged. If nothing checkable is left the person gets the extracts. If the model says the documents do not contain enough evidence, that is passed on. If the provider fails, the person gets extracts, and the provider's error text is logged but never shown.
- **The record.** Each question keeps its text, mode, answer, where every citation came from (document, page, passage), the provider and model for generated answers, and a flag. The prompt is never stored. History shows a person only their own questions and modes, not the answers, because their access may change after the answer was written. `assistant.asked` and `assistant.settings_changed` are audited.
- **Web.** An Ask page with the mode stated plainly, sources linking to where each document lives, notices, a warning when an answer was flagged, and settings for administrators.

## Not in this slice
- **Embeddings and hybrid search.** Retrieval is keyword-only. There is no embedding provider, no vector column and no pgvector, so a question that uses different words from the document will not find it. The provider interface is built for the model only.
- A local model is untested here: the development machine cannot run one, and no real provider account was used. The adapters are tested against stand-in servers, so the first call to a real service is the first real proof.
- Optional redaction of personal data before sending, and limiting by classification rather than only by cabinet.
- Streaming answers, follow-up questions that keep context, and rating answers.
- Suggested workflow actions, and any use of the assistant to change data.
- Limits on how often a person can ask. Each question costs a search and possibly a paid call.
- Legal review: enabling a foreign provider for real documents needs it (ADR 0013).

## Consequences
- Because only passages the person may see are retrieved, a permission change takes effect on the next question with no clean-up. A passage's text is shown as a snippet, so the snippet needs the same right as the text.
- The half-the-words rule keeps weak matches out but also refuses questions that mix two topics, which then need to be asked separately.
- Generated answers can still be wrong in ways a citation check cannot catch, for example a true quotation used for a false claim. The sources are shown so a person can check.
