# FAILURES.md — Known Failure Modes & Edge Cases

Here are the 4 honest failure modes under which our system can still lose a DM, send a duplicate, or report a wrong number:

1. **In-memory queue drop during process restart or container sleep**:
   If the server restarts or Render spins down while a DM is queued in the `asyncio.Queue` (after `/webhook` returned `200 OK` but before `POST /v1/dm/send` executed), that DM is lost. Nothing on disk knows it was in flight.

2. **Race condition on high-frequency concurrent webhooks (<10ms window)**:
   Two identical webhook events (or different comments from the same user matching the same rule) arriving within ~10ms can both pass the duplicate check before either writes to SQLite. Both get enqueued to the DM worker, relying entirely on the mock API's `Idempotency-Key` header (`dm_{user_id}_{rule_id}`) to prevent double-sending.

3. **`comment.deleted` arriving after API acceptance**:
   If a `comment.deleted` event arrives *after* `POST /v1/dm/send` has already returned `202 Accepted` (or `200 OK`) but before the status reconciler checks `GET /v1/dm/{dm_id}`, the DM has already been accepted by the platform API and cannot be recalled or un-sent.

4. **Ephemeral storage database reset on host redeployment**:
   Because SQLite runs on local disk without a persistent volume on Render's free tier, every code deployment or cold restart wipes `app.db`. Any active rules or pending retry state from previous runs are cleared unless re-created via `POST /rules`.

---

## Technical Mitigations Included
- **SQLite WAL Mode & `UNIQUE(user_id, rule_id)`**: Enforces strict single DM per user per rule across standard runs.
- **Sliding Window Rate Limiter (9 req / 60s)**: Safely stays below the 10 req/60s limit with automatic pause on `429 Retry-After`.
- **Idempotency Header**: Every `POST /v1/dm/send` includes `Idempotency-Key: dm_{user_id}_{rule_id}` to prevent mock API double-sending.
- **Polling Status Reconciler**: Background task polls `GET /v1/dm/{dm_id}` every 3s to detect accepted DMs that flip to `failed` and automatically re-queues them.
