# System Failure Modes & Known Edge Cases

While the system is designed to handle hostile API conditions (rate limits, 500 errors, duplicate webhooks, out-of-order events, and silent DM failures), the following specific scenarios could still result in a lost DM, duplicate DM, or stats mismatch:

### 1. In-Memory Queue Loss During Process Termination / Sudden Crash
- **Condition:** If the application process receives a SIGKILL (`kill -9`) or host crash while an event has been acknowledged with `200 OK` on `/webhook` but before the background task executes the `INSERT INTO dm_log` transaction.
- **Impact:** The webhook event is lost because it was acknowledged to the sender but not yet committed to SQLite disk storage.

### 2. High-Frequency Concurrent Webhooks for the Same User/Rule Race Condition
- **Condition:** Two identical webhook events (or different comments from the same user matching the same rule) arrive concurrently within <2ms on separate worker threads/coroutines.
- **Impact:** If both check `dm_log` status before either transaction commits, both could attempt to enqueue a DM. Although `UNIQUE(user_id, rule_id)` in SQLite prevents double DB insertion and triggers `IntegrityError` on the second transaction, if the second call fails after the API request is initiated (in rare race edge-cases), duplicate handling relies on the Mock API's `Idempotency-Key` header (`dm_{user_id}_{rule_id}`). If the API fails to respect idempotency, a duplicate DM could be sent.

### 3. Asynchronous `duplicates_blocked` Counter Drifting on Abrupt Restart
- **Condition:** The `duplicates_blocked` metric is updated in memory immediately for performance and flushed to SQLite asynchronously.
- **Impact:** If the application crashes immediately after blocking a duplicate before the background SQLite `UPDATE stats_counter` completes, `GET /stats` after restart will under-report `duplicates_blocked` by the uncommitted delta.

### 4. `comment.deleted` Event Delayed Beyond DM Delivery Window
- **Condition:** A user comments `PRICE` and immediately deletes the comment within 100ms. If the `comment.created` event is processed and the DM API call (`POST /v1/dm/send`) completes *before* the `comment.deleted` webhook arrives at `/webhook`.
- **Impact:** The DM will be sent to the user even though the comment was deleted, because our service cannot retroactively recall a DM already accepted/sent by Instagram's API.

---

## Technical Mitigations Included
- **SQLite WAL Mode & `UNIQUE(user_id, rule_id)`**: Enforces strict single DM per user per rule across restarts.
- **Sliding Window Rate Limiter (9 req / 60s)**: Safely stays below the 10 req/60s limit with automatic pause on `429 Retry-After`.
- **Idempotency Header**: Every `POST /v1/dm/send` includes `Idempotency-Key: dm_{user_id}_{rule_id}` to prevent mock API double-sending.
- **Polling Reconciler**: Background task polls `GET /v1/dm/{dm_id}` to catch accepted DMs that flip to `failed` and automatically re-queues them.
