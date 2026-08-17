import asyncio
import time
import logging
import httpx
from collections import deque
from typing import Optional, Dict, Any
from config import MOCK_API_BASE_URL
import config
from db import get_db

logger = logging.getLogger(__name__)

class RateLimiter:
    """Sliding window rate limiter: 10 requests per rolling 60 seconds."""
    def __init__(self, max_requests: int = 10, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.timestamps = deque()
        self.lock = asyncio.Lock()
        self.pause_until: float = 0.0

    async def acquire(self):
        async with self.lock:
            while True:
                now = time.time()
                # Check if paused due to 429
                if now < self.pause_until:
                    sleep_dur = self.pause_until - now
                    logger.info(f"Rate limiter paused due to 429. Sleeping for {sleep_dur:.2f}s")
                    await asyncio.sleep(sleep_dur)
                    continue

                # Evict timestamps outside rolling window
                while self.timestamps and self.timestamps[0] <= now - self.window_seconds:
                    self.timestamps.popleft()

                if len(self.timestamps) < self.max_requests:
                    self.timestamps.append(now)
                    return

                # Need to wait until oldest timestamp expires
                oldest = self.timestamps[0]
                wait_time = oldest + self.window_seconds - now + 0.05
                if wait_time > 0:
                    logger.debug(f"Rate limit reached ({len(self.timestamps)} reqs in 60s). Waiting {wait_time:.2f}s")
                    await asyncio.sleep(wait_time)

    def pause(self, seconds: float):
        now = time.time()
        self.pause_until = max(self.pause_until, now + seconds)

rate_limiter = RateLimiter(max_requests=9, window_seconds=60.0) # Using 9 to stay safely under limit of 10

class DMSender:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.client: Optional[httpx.AsyncClient] = None
        self._worker_task: Optional[asyncio.Task] = None

    async def start(self):
        self.client = httpx.AsyncClient(timeout=10.0)
        self._worker_task = asyncio.create_task(self._process_queue())
        logger.info("DM Sender queue worker started.")

    async def stop(self):
        if self._worker_task:
            self._worker_task.cancel()
        if self.client:
            await self.client.aclose()

    async def enqueue_dm(self, dm_task: Dict[str, Any]):
        """dm_task = {'db_id': int, 'user_id': str, 'rule_id': str, 'comment_id': str, 'dm_message': str, 'idempotency_key': str, 'attempts': int}"""
        await self.queue.put(dm_task)

    async def _process_queue(self):
        while True:
            item = await self.queue.get()
            try:
                await self._send_with_retry(item)
            except Exception as e:
                logger.error(f"Unexpected error processing DM item {item}: {e}", exc_info=True)
            finally:
                self.queue.task_done()

    async def _check_if_comment_deleted(self, comment_id: str) -> bool:
        db = await get_db()
        try:
            async with db.execute("SELECT comment_id FROM deleted_comments WHERE comment_id = ?", (comment_id,)) as cursor:
                row = await cursor.fetchone()
                return row is not None
        finally:
            await db.close()

    async def _send_with_retry(self, item: Dict[str, Any]):
        db_id = item['db_id']
        user_id = item['user_id']
        comment_id = item['comment_id']
        message = item['dm_message']
        idempotency_key = item['idempotency_key']
        attempts = item.get('attempts', 0)

        # Check if comment was deleted before sending
        if await self._check_if_comment_deleted(comment_id):
            logger.info(f"Comment {comment_id} was deleted before DM send. Cancelling DM for user {user_id}.")
            db = await get_db()
            try:
                await db.execute("UPDATE dm_log SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (db_id,))
                await db.commit()
            finally:
                await db.close()
            return

        max_attempts = 10
        backoff = 1.0

        url = f"{MOCK_API_BASE_URL}/v1/dm/send"
        headers = {
            "X-API-Key": config.get_api_key(),
            "Idempotency-Key": idempotency_key,
            "Content-Type": "application/json"
        }
        payload = {
            "recipient_user_id": user_id,
            "message": message,
            "comment_id": comment_id
        }

        while attempts < max_attempts:
            attempts += 1

            # Update attempts count in DB
            db = await get_db()
            try:
                await db.execute("UPDATE dm_log SET attempts = ? WHERE id = ?", (attempts, db_id))
                await db.commit()
            finally:
                await db.close()

            # Acquire rate limit slot
            await rate_limiter.acquire()

            # Double check comment deletion right before sending
            if await self._check_if_comment_deleted(comment_id):
                logger.info(f"Comment {comment_id} deleted right before API call. Cancelling.")
                db = await get_db()
                try:
                    await db.execute("UPDATE dm_log SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (db_id,))
                    await db.commit()
                finally:
                    await db.close()
                return

            try:
                response = await self.client.post(url, headers=headers, json=payload)
                
                if response.status_code in (200, 202):
                    data = response.json()
                    dm_id = data.get("dm_id")
                    logger.info(f"DM send accepted for user {user_id}, dm_id={dm_id}")
                    
                    db = await get_db()
                    try:
                        await db.execute(
                            "UPDATE dm_log SET status = 'accepted', dm_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (dm_id, db_id)
                        )
                        await db.commit()
                    finally:
                        await db.close()
                    return

                elif response.status_code == 429:
                    retry_after = 60.0
                    try:
                        retry_after = float(response.headers.get("Retry-After", 60.0))
                    except ValueError:
                        pass
                    logger.warning(f"Rate limited (429). Pausing for {retry_after}s.")
                    rate_limiter.pause(retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                elif response.status_code == 400:
                    detail = response.text
                    logger.error(f"Invalid DM request (400) for user {user_id}: {detail}")
                    db = await get_db()
                    try:
                        await db.execute(
                            "UPDATE dm_log SET status = 'failed', error_detail = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (f"400 Bad Request: {detail}", db_id)
                        )
                        await db.commit()
                    finally:
                        await db.close()
                    return

                elif response.status_code == 500:
                    logger.warning(f"Mock API returned 500 for user {user_id}. Attempt {attempts}/{max_attempts}. Backing off {backoff}s.")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
                    continue

                else:
                    logger.warning(f"Unexpected status code {response.status_code} for DM send: {response.text}")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
                    continue

            except (httpx.RequestError, httpx.TimeoutException) as exc:
                logger.warning(f"Network error sending DM (attempt {attempts}): {exc}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue

        # If max_attempts reached
        logger.error(f"Max attempts ({max_attempts}) reached for DM id={db_id}, user={user_id}. Marking failed.")
        db = await get_db()
        try:
            await db.execute(
                "UPDATE dm_log SET status = 'failed', error_detail = 'Max retries exceeded', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (db_id,)
            )
            await db.commit()
        finally:
            await db.close()

dm_sender = DMSender()
