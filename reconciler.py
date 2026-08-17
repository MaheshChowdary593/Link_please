import asyncio
import logging
import httpx
from typing import Optional
from config import MOCK_API_BASE_URL
import config
from db import get_db
from dm_sender import dm_sender

logger = logging.getLogger(__name__)

class StatusReconciler:
    def __init__(self, interval_seconds: float = 5.0):
        self.interval_seconds = interval_seconds
        self.client: Optional[httpx.AsyncClient] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        self.client = httpx.AsyncClient(timeout=10.0)
        self._task = asyncio.create_task(self._reconcile_loop())
        logger.info("Status reconciler started.")

    async def stop(self):
        if self._task:
            self._task.cancel()
        if self.client:
            await self.client.aclose()

    async def _reconcile_loop(self):
        while True:
            try:
                await self._run_reconciliation()
            except Exception as e:
                logger.error(f"Error in reconciliation loop: {e}", exc_info=True)
            await asyncio.sleep(self.interval_seconds)

    async def _run_reconciliation(self):
        db = await get_db()
        pending_dms = []
        try:
            # Query all DMs that are in 'accepted' state with a valid dm_id
            async with db.execute(
                "SELECT id, dm_id, user_id, rule_id, comment_id, attempts, idempotency_key FROM dm_log WHERE status = 'accepted' AND dm_id IS NOT NULL"
            ) as cursor:
                rows = await cursor.fetchall()
                pending_dms = [dict(row) for row in rows]
        finally:
            await db.close()

        if not pending_dms:
            return

        headers = {"X-API-Key": config.get_api_key()}

        for dm_item in pending_dms:
            dm_id = dm_item['dm_id']
            db_id = dm_item['id']
            url = f"{MOCK_API_BASE_URL}/v1/dm/{dm_id}"

            try:
                resp = await self.client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("status")

                    if status == "delivered":
                        logger.info(f"Reconciled dm_id={dm_id}: status=DELIVERED")
                        db = await get_db()
                        try:
                            await db.execute(
                                "UPDATE dm_log SET status = 'delivered', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                (db_id,)
                            )
                            await db.commit()
                        finally:
                            await db.close()

                    elif status == "failed":
                        attempts = dm_item['attempts']
                        if attempts < 10:
                            logger.warning(f"Reconciled dm_id={dm_id}: status=FAILED from API. Retrying DM (attempt {attempts}).")
                            # Reset status to queued and clear dm_id so it gets re-sent
                            # Note: update idempotency key slightly or keep same?
                            # Keeping same idempotency_key allows API to handle it or re-send
                            db = await get_db()
                            rule_message = ""
                            try:
                                async with db.execute("SELECT dm_message FROM rules WHERE rule_id = ?", (dm_item['rule_id'],)) as cur:
                                    r_row = await cur.fetchone()
                                    if r_row:
                                        rule_message = r_row['dm_message']

                                await db.execute(
                                    "UPDATE dm_log SET status = 'queued', dm_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                    (db_id,)
                                )
                                await db.commit()
                            finally:
                                await db.close()

                            if rule_message:
                                await dm_sender.enqueue_dm({
                                    'db_id': db_id,
                                    'user_id': dm_item['user_id'],
                                    'rule_id': dm_item['rule_id'],
                                    'comment_id': dm_item['comment_id'],
                                    'dm_message': rule_message,
                                    'idempotency_key': f"{dm_item['idempotency_key']}_retry{attempts}",
                                    'attempts': attempts
                                })
                        else:
                            logger.error(f"Reconciled dm_id={dm_id}: status=FAILED permanently after {attempts} attempts.")
                            db = await get_db()
                            try:
                                await db.execute(
                                    "UPDATE dm_log SET status = 'failed', error_detail = 'API status failed', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                    (db_id,)
                                )
                                await db.commit()
                            finally:
                                await db.close()

                    elif status == "queued":
                        # Still queued on mock API side
                        pass

            except Exception as e:
                logger.warning(f"Error checking dm_id={dm_id}: {e}")

reconciler = StatusReconciler(interval_seconds=3.0)
