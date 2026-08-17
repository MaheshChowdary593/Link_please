import aiosqlite
import logging
from config import DB_PATH

logger = logging.getLogger(__name__)

async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL;")
    await db.execute("PRAGMA busy_timeout=5000;")
    await db.execute("PRAGMA synchronous=NORMAL;")
    return db

async def init_db():
    db = await get_db()
    try:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS seen_events (
                event_id TEXT PRIMARY KEY,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS rules (
                rule_id TEXT PRIMARY KEY,
                keyword TEXT NOT NULL,
                dm_message TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS dm_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                comment_id TEXT NOT NULL,
                dm_id TEXT,
                status TEXT NOT NULL, -- 'queued', 'accepted', 'delivered', 'failed', 'cancelled'
                attempts INTEGER DEFAULT 0,
                idempotency_key TEXT NOT NULL,
                error_detail TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, rule_id)
            );

            CREATE INDEX IF NOT EXISTS idx_dm_log_status ON dm_log(status);
            CREATE INDEX IF NOT EXISTS idx_dm_log_comment ON dm_log(comment_id);
            CREATE INDEX IF NOT EXISTS idx_dm_log_dm_id ON dm_log(dm_id);

            CREATE TABLE IF NOT EXISTS deleted_comments (
                comment_id TEXT PRIMARY KEY,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS stats_counter (
                key TEXT PRIMARY KEY,
                value INTEGER DEFAULT 0
            );

            INSERT OR IGNORE INTO stats_counter (key, value) VALUES ('duplicates_blocked', 0);
        """)
        await db.commit()
        logger.info("Database initialized successfully.")
    finally:
        await db.close()
