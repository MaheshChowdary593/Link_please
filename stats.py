import asyncio
import logging
from db import get_db

logger = logging.getLogger(__name__)

class StatsManager:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.sent = 0
        self.failed = 0
        self.queued = 0
        self.duplicates_blocked = 0

    async def init_stats(self):
        db = await get_db()
        try:
            # Load duplicates_blocked from stats_counter
            async with db.execute("SELECT value FROM stats_counter WHERE key = 'duplicates_blocked'") as cursor:
                row = await cursor.fetchone()
                if row:
                    self.duplicates_blocked = row["value"]

            # Count statuses from dm_log
            async with db.execute("SELECT status, COUNT(*) as cnt FROM dm_log GROUP BY status") as cursor:
                rows = await cursor.fetchall()
                counts = {row["status"]: row["cnt"] for row in rows}

            # 'delivered' -> sent
            self.sent = counts.get("delivered", 0)
            # 'failed' -> failed
            self.failed = counts.get("failed", 0)
            # 'queued', 'accepted' -> queued
            self.queued = counts.get("queued", 0) + counts.get("accepted", 0)

            logger.info(
                f"Stats initialized: sent={self.sent}, failed={self.failed}, "
                f"queued={self.queued}, duplicates_blocked={self.duplicates_blocked}"
            )
        finally:
            await db.close()

    async def increment_duplicates_blocked(self, count: int = 1):
        async with self._lock:
            self.duplicates_blocked += count
            current_val = self.duplicates_blocked

        # Async update DB without blocking endpoint
        db = await get_db()
        try:
            await db.execute(
                "UPDATE stats_counter SET value = ? WHERE key = 'duplicates_blocked'",
                (current_val,)
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Error persisting duplicates_blocked: {e}")
        finally:
            await db.close()

    async def get_stats_dict(self) -> dict:
        # For highest accuracy, query dm_log status counts live or return cached
        db = await get_db()
        try:
            async with db.execute("SELECT status, COUNT(*) as cnt FROM dm_log GROUP BY status") as cursor:
                rows = await cursor.fetchall()
                counts = {row["status"]: row["cnt"] for row in rows}

            async with db.execute("SELECT value FROM stats_counter WHERE key = 'duplicates_blocked'") as cursor:
                dup_row = await cursor.fetchone()
                duplicates_blocked = dup_row["value"] if dup_row else self.duplicates_blocked

            sent = counts.get("delivered", 0)
            failed = counts.get("failed", 0)
            queued = counts.get("queued", 0) + counts.get("accepted", 0)

            return {
                "sent": sent,
                "failed": failed,
                "queued": queued,
                "duplicates_blocked": duplicates_blocked
            }
        except Exception as e:
            logger.error(f"Error getting stats from DB: {e}")
            async with self._lock:
                return {
                    "sent": self.sent,
                    "failed": self.failed,
                    "queued": self.queued,
                    "duplicates_blocked": self.duplicates_blocked
                }
        finally:
            await db.close()

stats_manager = StatsManager()
