# ADR 0005: Server-side sessions; API keys for machines

**Status:** Accepted on 2026-11-06, owner accepted the plan defaults.

## Context
Sessions must be revocable immediately and device-manageable. Long-lived JWTs cannot be revoked without a server-side list, which removes their main advantage.

## Decision
- **Browser:** opaque session ID in a `Secure`, `HttpOnly`, `SameSite=Lax` cookie, with only a hash stored server-side. Unsafe requests also need a CSRF token.
- **Machines:** hashed, scoped, expiring API keys tied to a service identity, plus OAuth client credentials where an integration needs it. Machines use the same policy service as users.
- **Passwords:** Argon2id through `argon2-cffi`, with a configurable policy and lockout plus rate limiting.
- **MFA:** TOTP with one-time recovery codes; secrets encrypted at rest.
- **SSO:** OIDC through Authlib, with group-to-role mapping per tenant. Tests use a local OIDC server.
- **Bootstrap:** the first administrator is created by a one-time CLI command, never by a default password.

## Consequences
- Same-site deployment of the web client and API is the simple path. Cross-origin setups need explicit CORS configuration.
- Session lookups add a small per-request cost, which Redis caching can offset.
