# LinkPlease Tech Intern Assignment — DM Automation Service

A high-performance, resilient Instagram DM automation service built with **FastAPI**, **SQLite (aiosqlite)**, and **httpx**.

Handles hostile API behaviors including rate limits (10 req/60s), random 500 errors, duplicate webhooks, out-of-order events, silent DM delivery failures, and `comment.deleted` events.

---

## Features

### Part A (Required)
- `POST /rules`: Create case-insensitive keyword rules (`{ "keyword": "PRICE", "dm_message": "..." }`)
- `POST /webhook`: Webhook listener returning `200 OK` within milliseconds while background processing events
- **Deduplication**: `UNIQUE(user_id, rule_id)` constraint ensures a user is never DMed twice for the same rule
- **Resilience**: Sliding-window rate limiter + exponential backoff retry on 500s + `Retry-After` handling on 429s

### Part B
- **Webhook Signature Verification**: HMAC-SHA256 verification using `X-PseudoGram-Signature` header
- `GET /stats`: Accurate live stats (`sent`, `failed`, `queued`, `duplicates_blocked`) backed by SQLite WAL database

### Part C
- **Delivery Reconciliation**: Background task polls `GET /v1/dm/{dm_id}` to detect DMs that failed after being accepted (202) and automatically retries them
- **`comment.deleted` Handling**: Suppresses pending/queued DMs if deletion event arrives before sending
- **High Concurrency**: Built with `asyncio` task queues and WAL mode SQLite for zero event loss under 500-comment load bursts

---

## API Contract

| Route | Method | Description |
|---|---|---|
| `/webhook` | `POST` | Receives comment events. Returns `200` immediately. |
| `/rules` | `POST` | Creates a new keyword rule. Returns `201 Created`. |
| `/stats` | `GET` | Returns live server stats. |

---

## Environment Variables

Create a `.env` file or export environment variables:

```env
API_KEY=your_pseudogram_api_key
MOCK_API_BASE_URL=https://pseudogram-api.onrender.com
DB_PATH=app.db
PORT=8000
VERIFY_SIGNATURE=true
```

---

## Running Locally

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the application:
```bash
python main.py
```
Or with uvicorn:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## Testing & Verification

1. Start server locally or deploy to public host (e.g. Railway, Render, Fly.io).
2. Create a rule:
```bash
curl -X POST http://localhost:8000/rules \
  -H "Content-Type: application/json" \
  -d '{"keyword": "PRICE", "dm_message": "Here is the price list: $99"}'
```
3. Run simulation using mock API:
```bash
curl -X POST https://pseudogram-api.onrender.com/v1/simulate/start \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"webhook_url": "https://YOUR-APP.up.railway.app/webhook", "count": 500, "duration_seconds": 10}'
```
4. Query stats:
```bash
curl http://localhost:8000/stats
```
5. Check truth log from mock API:
```bash
curl https://pseudogram-api.onrender.com/v1/simulate/{RUN_ID}/truth \
  -H "X-API-Key: YOUR_API_KEY"
```
