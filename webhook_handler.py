import hmac
import hashlib
import logging
import sqlite3
from typing import Dict, Any
from db import get_db
from rules import rule_manager
from dm_sender import dm_sender
from stats import stats_manager
import config

logger = logging.getLogger(__name__)

def verify_signature(raw_body: bytes, signature_header: str) -> bool:
    if not config.get_verify_signature():
        return True
    api_key = config.get_api_key()
    if not api_key or not signature_header:
        return False
    
    sig = signature_header
    if sig.startswith("sha256="):
        sig = sig[7:]
        
    expected = hmac.new(
        api_key.encode('utf-8'),
        raw_body,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(sig.lower(), expected.lower())

async def process_webhook_payload(payload: Dict[str, Any]):
    """Processes event asynchronously in background so /webhook can respond immediately."""
    event_id = payload.get("event_id")
    event_type = payload.get("event_type")
    data = payload.get("data", {})

    if not event_id or not event_type:
        logger.warning(f"Invalid webhook payload missing event_id or event_type: {payload}")
        return

    # Step 1: Dedup event_id
    db = await get_db()
    is_duplicate_event = False
    try:
        try:
            await db.execute("INSERT INTO seen_events (event_id) VALUES (?)", (event_id,))
            await db.commit()
        except sqlite3.IntegrityError:
            is_duplicate_event = True
    finally:
        await db.close()

    if is_duplicate_event:
        logger.info(f"Duplicate event_id received: {event_id}")
        if event_type == "comment.created":
            text = data.get("text", "")
            if rule_manager.match_rules(text):
                await stats_manager.increment_duplicates_blocked()
        return

    # Step 2: Process by event type
    if event_type == "comment.deleted":
        comment_id = data.get("comment_id")
        if comment_id:
            logger.info(f"Processing comment.deleted for comment_id={comment_id}")
            db = await get_db()
            try:
                await db.execute("INSERT OR IGNORE INTO deleted_comments (comment_id) VALUES (?)", (comment_id,))
                await db.execute(
                    "UPDATE dm_log SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE comment_id = ? AND status = 'queued'",
                    (comment_id,)
                )
                await db.commit()
            except Exception as e:
                logger.error(f"Error handling comment.deleted: {e}")
            finally:
                await db.close()

    elif event_type == "comment.created":
        comment_id = data.get("comment_id")
        text = data.get("text", "")
        from_user = data.get("from", {})
        user_id = from_user.get("user_id")

        if not comment_id or not user_id or not text:
            logger.warning(f"Malformed comment.created payload: {data}")
            return

        # Check if comment was deleted
        db = await get_db()
        is_deleted = False
        try:
            async with db.execute("SELECT comment_id FROM deleted_comments WHERE comment_id = ?", (comment_id,)) as cursor:
                if await cursor.fetchone():
                    is_deleted = True
        finally:
            await db.close()

        if is_deleted:
            logger.info(f"Ignoring comment.created for comment_id={comment_id} because it was already deleted.")
            return

        # Match against rules
        matched_rules = rule_manager.match_rules(text)
        if not matched_rules:
            return

        for rule in matched_rules:
            rule_id = rule["rule_id"]
            dm_message = rule["dm_message"]
            idempotency_key = f"dm_{user_id}_{rule_id}"

            inserted = False
            db_id = None
            db = await get_db()
            try:
                cursor = await db.execute(
                    """INSERT INTO dm_log (user_id, rule_id, comment_id, status, idempotency_key)
                       VALUES (?, ?, ?, 'queued', ?)""",
                    (user_id, rule_id, comment_id, idempotency_key)
                )
                await db.commit()
                db_id = cursor.lastrowid
                inserted = True
            except sqlite3.IntegrityError:
                inserted = False
            finally:
                await db.close()

            if inserted and db_id:
                logger.info(f"Queued DM for user={user_id}, rule={rule_id}, comment={comment_id}, db_id={db_id}")
                await dm_sender.enqueue_dm({
                    'db_id': db_id,
                    'user_id': user_id,
                    'rule_id': rule_id,
                    'comment_id': comment_id,
                    'dm_message': dm_message,
                    'idempotency_key': idempotency_key,
                    'attempts': 0
                })
            else:
                logger.info(f"Duplicate DM blocked: user {user_id} already has a DM entry for rule {rule_id}")
                await stats_manager.increment_duplicates_blocked()

