import asyncio
import os
import tempfile
import unittest
import hmac, hashlib

import config

class TestLinkPleaseApp(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        config.DB_PATH = self.temp_db.name
        os.environ["DB_PATH"] = self.temp_db.name
        os.environ["VERIFY_SIGNATURE"] = "true"
        os.environ["API_KEY"] = "test_secret_key"

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            try:
                os.remove(self.temp_db.name)
            except Exception:
                pass

    def test_signature_verification(self):
        from webhook_handler import verify_signature
        body = b'{"event_id":"evt_1"}'
        secret = "test_secret_key"
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_signature(body, sig))

        bad_sig = "sha256=invalid_hash"
        self.assertFalse(verify_signature(body, bad_sig))

    def test_async_workflow(self):
        from db import init_db, get_db
        from rules import rule_manager
        from stats import stats_manager
        from webhook_handler import process_webhook_payload

        async def run_tests():
            await init_db()
            rule_manager._rules_cache = []
            await rule_manager.load_rules()
            await stats_manager.init_stats()

            # Test Rule creation & matching
            rule = await rule_manager.add_rule(keyword="PRICE", dm_message="Price details here")
            self.assertIn("rule_id", rule)
            matches = rule_manager.match_rules("Can you DM me the Price please?")
            self.assertEqual(len(matches), 1)

            # Test Webhook comment.created payload
            payload = {
                "event_id": "evt_1001",
                "event_type": "comment.created",
                "sent_at": "2026-08-10T09:14:22.481Z",
                "data": {
                    "comment_id": "cmt_1001",
                    "post_id": "post_1",
                    "text": "Send PRICE list",
                    "created_at": "2026-08-10T09:14:21.900Z",
                    "from": {
                        "user_id": "usr_999",
                        "username": "test_user"
                    }
                }
            }
            await process_webhook_payload(payload)

            # Check DM log
            db = await get_db()
            async with db.execute("SELECT * FROM dm_log WHERE user_id = 'usr_999'") as cursor:
                rows = await cursor.fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["status"], "queued")
            await db.close()

            # Test Duplicate webhook event for same user & rule
            payload2 = dict(payload)
            payload2["event_id"] = "evt_1002" # new event id, same comment & user
            await process_webhook_payload(payload2)

            stats = await stats_manager.get_stats_dict()
            self.assertEqual(stats["duplicates_blocked"], 1)

        asyncio.run(run_tests())

if __name__ == "__main__":
    unittest.main()
