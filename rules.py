import uuid
import logging
from typing import List, Dict, Optional
from db import get_db

logger = logging.getLogger(__name__)

class RuleManager:
    def __init__(self):
        self._rules_cache: List[Dict[str, str]] = []

    async def load_rules(self):
        db = await get_db()
        try:
            async with db.execute("SELECT rule_id, keyword, dm_message FROM rules") as cursor:
                rows = await cursor.fetchall()
                self._rules_cache = [
                    {
                        "rule_id": row["rule_id"],
                        "keyword": row["keyword"],
                        "dm_message": row["dm_message"]
                    }
                    for row in rows
                ]
            logger.info(f"Loaded {len(self._rules_cache)} rules into memory cache.")
        finally:
            await db.close()

    async def add_rule(self, keyword: str, dm_message: str) -> Dict[str, str]:
        rule_id = f"rule_{uuid.uuid4().hex[:12]}"
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO rules (rule_id, keyword, dm_message) VALUES (?, ?, ?)",
                (rule_id, keyword, dm_message)
            )
            await db.commit()
        finally:
            await db.close()

        rule = {
            "rule_id": rule_id,
            "keyword": keyword,
            "dm_message": dm_message
        }
        self._rules_cache.append(rule)
        return rule

    def match_rules(self, text: str) -> List[Dict[str, str]]:
        if not text:
            return []
        text_lower = text.lower()
        matched = []
        for rule in self._rules_cache:
            if rule["keyword"].lower() in text_lower:
                matched.append(rule)
        return matched

rule_manager = RuleManager()
