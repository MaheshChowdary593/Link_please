# LinkPlease Assignment — Submission & Failure Modes

## 1. Submission Payload (`POST /v1/submit`)

```json
{
  "email": "maheshmulaguri13@gmail.com",
  "github_repo": "https://github.com/MaheshChowdary593/Link_please",
  "working_url": "https://linkplease-dm-service.onrender.com",
  "loom_url": "https://drive.google.com/file/d/1D4-TJf_FTJwhn3uynKwnL-Cj1Z2HPM2V/view?usp=sharing",
  "start_date": "2026-08-17"
}
```

---

## 2. API Contract JSON Payloads

### `POST /webhook`
**Incoming Event Payload:**
```json
{
  "event_id": "evt_01J8ZQ4K2N7RXA",
  "event_type": "comment.created",
  "sent_at": "2026-08-10T09:14:22.481Z",
  "data": {
    "comment_id": "cmt_9f2a7c",
    "post_id": "post_44de1b",
    "text": "PRICE please 🙏",
    "created_at": "2026-08-10T09:14:21.900Z",
    "from": {
      "user_id": "usr_3b91fe",
      "username": "arjun.shoots"
    }
  }
}
```
**Response (200 OK):**
```json
{
  "status": "ok"
}
```

### `POST /rules`
**Request Payload:**
```json
{
  "keyword": "PRICE",
  "dm_message": "Here is the price list: $99"
}
```
**Response (201 Created):**
```json
{
  "rule_id": "rule_890dfdf03a47",
  "keyword": "PRICE",
  "dm_message": "Here is the price list: $99"
}
```

### `GET /stats`
**Response (200 OK):**
```json
{
  "sent": 13,
  "failed": 0,
  "queued": 66,
  "duplicates_blocked": 47
}
```

### `POST /v1/dm/send` (Mock API Outbound)
**Request Payload:**
```json
{
  "recipient_user_id": "usr_3b91fe",
  "message": "Here is the price list: $99",
  "comment_id": "cmt_9f2a7c"
}
```
**Response (200 / 202 Accepted):**
```json
{
  "dm_id": "dm_7c1f0a",
  "status": "queued"
}
```

### `GET /v1/dm/{dm_id}` (Mock API Reconciliation Status)
**Response (200 OK):**
```json
{
  "dm_id": "dm_7c1f0a",
  "status": "delivered",
  "recipient_user_id": "usr_3b91fe",
  "updated_at": "2026-08-10T09:14:31.002Z"
}
```

---

## 3. Known Failure Modes & Edge Cases

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

## 4. Technical Mitigations Included
- **SQLite WAL Mode & `UNIQUE(user_id, rule_id)`**: Enforces strict single DM per user per rule across standard runs.
- **Sliding Window Rate Limiter (9 req / 60s)**: Safely stays below the 10 req/60s limit with automatic pause on `429 Retry-After`.
- **Idempotency Header**: Every `POST /v1/dm/send` includes `Idempotency-Key: dm_{user_id}_{rule_id}` to prevent mock API double-sending.
- **Polling Status Reconciler**: Background task polls `GET /v1/dm/{dm_id}` every 3s to detect accepted DMs that flip to `failed` and automatically re-queues them.
