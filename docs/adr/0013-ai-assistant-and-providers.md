# ADR 0013: Provider adapters, RAG, extract-only fallback

**Status:** Accepted on 2026-11-06, owner accepted the plan defaults.

## Context
The assistant must cite sources, refuse without evidence, never reveal unauthorised content and work without a subscription. Documents may also contain text that tries to manipulate it.

## Decision

### Providers
Separate `EmbeddingProvider` and `LlmProvider` interfaces. Adapters cover a local OpenAI-compatible server such as Ollama, an OpenAI-compatible cloud API, and Anthropic. Providers are configured per tenant and behind feature flags.

### Pipeline
1. Compute the user's permission filter.
2. Retrieve chunks through the same search layer. There is no separate path.
3. If evidence is below a threshold, answer that the documents do not contain enough evidence, without calling the LLM.
4. Build context with document ID, page and section for each chunk.
5. Generate, then validate that every citation refers to a supplied chunk. Uncited claims are removed or the answer is flagged.
6. Store citations and metadata. Prompt text is stored only if the tenant's policy allows it.

### Fallback
With no LLM, the assistant returns ranked passages with citations, clearly labelled as extracts rather than generated answers.

### External provider policy
Default off. Enabling is per tenant and can be limited by cabinet or classification. Blocked documents are excluded and the UI says so. Optional PII redaction applies before sending. Because of Rwanda's cross-border data rules, enabling a foreign provider needs legal review.

### Prompt injection
- Document text is untrusted data and is delimited as such.
- The model has **no tools** and cannot act; it receives only authorised chunks.
- This limits the damage, but it does not stop a malicious document from distorting an answer. Citations let users check.
- Outputs are escaped when rendered, preventing injected links or scripts.

## Consequences
- Permission safety comes from retrieval, not model behaviour.
- The local LLM will not run usefully on the 7.8 GB development machine, so generated-answer demos need a provider or larger hardware.
- Workflow suggestions are advisory and need human confirmation.
