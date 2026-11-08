# ADR 0035: Signed webhooks from the audit trail, and idempotency keys

**Status:** Accepted on 2026-11-08 (F20, first slice)

## Context
Other systems need to hear what happens here and to write safely over an unreliable network. API keys already exist (F01). Connectors to real systems need the owner's accounts, so this slice builds the parts that stand alone: outbound events and retry-safe writes.

## Decision
- **Events come from the audit trail, and only from it.** The audit log now has listeners: functions called, inside the transaction that wrote an event, for every event. The webhook service is one. A listener runs in its own savepoint, so a failure is logged and rolled back without ever undoing the audited action. A listener has a key, and adding one with a key already in use replaces the old, so re-wiring never makes events fire twice. The platform knows nothing of webhooks.
- **A short list of events.** A subscription chooses from 23 events (document filed, new version, indexed, commented, stamped, disposed; task created, updated, escalated; workflow started, step decided, finished; signed; forms). Reads, downloads, sign-ins, audit events and the webhooks' own events cannot be subscribed to, so a webhook cannot report on who looked at what, or on itself.
- **Payloads carry no content.** Each says the API version, the event's id, its type, when it happened, and the type and id of the object it was about. A receiver asks the API with its own key for anything more, so a webhook can never reveal more than that key may see. A test sends a comment about a "merger" on a document named "secret-merger" and checks neither word leaves.
- **Safe addresses.** An address must be https (plain http only if the server is set to allow it, for development), have no user name or password, and every address its name resolves to must be public. Loopback, private ranges, link-local (including the cloud metadata service), carrier-grade, multicast and IPv4-mapped IPv6 are refused. It is checked when saved and again before every delivery, because a name can later point somewhere else. A delivery whose address has become unsafe fails at once, with no retries. Redirects are never followed.
- **Signed calls.** Each call carries `X-DocuFlow-Signature: t=<time>,v1=<HMAC-SHA256 of "<time>." and the body>`, with the event and delivery ids in headers. A receiver checks the signature and that the time is within five minutes. The body is compact JSON with keys in a fixed order, so both sides sign the same bytes.
- **Secrets.** The signing secret is made by the server, shown once on creation or replacement, and kept encrypted with the organization's key. Replacing it stops the old one working at once.
- **Delivery.** A background job on the `integrations` queue, tried up to five times with growing waits, then marked failed with the reason and never sent again by itself. Network errors are recorded by type only, not their text. A person can send a failed delivery again, which makes a new delivery that points at the old one, and can send a test ping. The delivery log shows status, tries and the error.
- **Idempotency keys.** On a write (POST, PUT, PATCH, DELETE under `/api/v1`) with an `Idempotency-Key` header and a JSON body up to 1 MiB, the first answer is kept for a day and returned again for a repeat, marked `Idempotent-Replay: true`. The key belongs to the caller (a hash of their credential), so one caller can never read another's answer. The same key with a different request is refused with 422. A request still being worked on is refused with 409 rather than started twice. Only a successful answer is kept, so a failed request can be corrected and sent again with the same key. A file upload with a key is not covered and the answer says so. Sign-in, API keys and webhooks are never kept, because their answers hold secrets.
- **Where it is stored.** The idempotency table is not tied to an organization, since it is read before anyone is identified. The application's own database role is refused access to it, and only the platform role uses it.
- **Rights and audit.** Managing webhooks needs `integrations.manage`. `webhook.created`, `.updated`, `.deleted`, `.secret_rotated` and `.redelivered` are audited, never with a secret.
- **Web.** A Webhooks page: add one with the events to send, send a test, switch off, replace the secret, delete, and see recent deliveries with a send-again button.

## Not in this slice
- **Connectors to real systems** (accounting, directory, mail, Microsoft 365). They need the owner's accounts and are listed as external dependencies.
- **DNS-rebinding between the check and the call.** The address is resolved and checked, then the HTTP client resolves it again. Connecting to the checked address, with the host name still used for TLS, would close that gap.
- Idempotency for file uploads (needs the body hashed as it streams), and a retention policy other than one day.
- Scoped API keys: a key has the rights of its service user. Limits per key and per minute are not built.
- Switching a webhook off automatically after repeated failures, and delivery metrics.
- Event ordering guarantees. Deliveries can arrive out of order or twice, so receivers should use the event id.
- A published OpenAPI document for integrators beyond the generated one at `/api/v1/docs`.

## Consequences
- Because events are audit events, adding an action to the list of subscribable events is one line, and anything recorded in the trail can become an event.
- The audit listener runs on every recorded event. It is cheap for events not on the list, but each listed event costs one query for subscriptions.
- Delivery is at least once. A receiver that is slow or sometimes down will see repeats and late events.
