# M1 gate report

**Result:** Ready for owner review, not yet signed off | **CI run:** #17 | **Commit:** `a51cb30`

Gate from the plan: Finance and HR isolation demo; a role change takes effect immediately. F01 and F02 stay **In progress** in the matrix until the owner accepts this gate and the open links below are decided.

| Gate item | Result |
|---|---|
| Finance and HR each see only their own cabinet, tree and counts | Passed (browser journeys) |
| Another department's cabinet opened by its address reveals nothing | Passed (browser journey) |
| A permission change applies to the very next page load | Passed (browser journey) |
| Lint, types, module boundaries, licence policy, vulnerability audit | Passed in CI #17 |
| Backend tests | 257 passed, 3 skipped locally (the skipped ones need a real S3 backend); backend job green in CI #17 |
| Web unit tests | 46 passed |
| Browser journeys in a real browser against the real API | 27 passed, 3 skipped (screenshot gallery, opt-in); `e2e` job green in CI #17 |
| Clean `docker compose` stack | Passed (`compose` job, CI #17) |

Journeys cover onboarding and disabling a user, two-step sign-in with a recovery code, API keys, cross-site request protection, the content policy, filing structure (nested folders, rename, move, copy, archive, delete) and accessibility checks.

## Problems found while closing the gate

| Problem | Fix |
|---|---|
| The next person to sign in landed on the previous person's page after a deliberate sign-out | The return address is kept only for expired sessions |
| Action menus closed or stayed open wrongly | They close on an outside click and on Escape |
| Sessions showed raw browser strings | Shown as, for example, "Edge on Windows" |
| Tables pushed actions off phone screens | Least important columns hide on narrow screens |
| Demo rate limiter never expired its windows | Windows now end after the set time |
| The e2e job failed once in CI, most likely from the sign-in rate limit (inferred from the timing, not from the failure message; the next run passed) | Automated runs no longer count sign-in attempts |

## Open items

- **OIDC single sign-on adapter** (F01) is not built.
- **Upload, retention, auto-routing and document copy** wait for F03, F15, F07 and F11.
- **Readiness ignores workers.** Carried over from the M0 gate; no fix found in the code.
- **Local Docker is still blocked** on the owner's laptop (see the M0 report).
