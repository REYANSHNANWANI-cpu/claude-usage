"""Unit tests for Quota Manager and enforcement mechanisms."""
import os
import tempfile
import unittest
from pathlib import Path

from claude_sentinel.database import DatabaseManager
from claude_sentinel.quota_manager import QuotaManager


class TestQuotaManager(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.temp_dir.name, "test_quota.db")
        self.db = DatabaseManager(self.db_path)

        self.breaches = []
        self.actions = []

        self.manager = QuotaManager(
            db=self.db,
            on_quota_breach=lambda b: self.breaches.append(b),
            on_action_executed=lambda a: self.actions.append(a),
        )

    def tearDown(self):
        self.db.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_no_breach_when_under_ceiling(self):
        """Verify no breach is triggered when tallies are below limits."""
        breaches = self.manager.check_and_evaluate()
        self.assertEqual(len(breaches), 0)
        self.assertFalse(self.manager.is_network_blocked())

    def test_breach_detection_and_percentage(self):
        """Verify breach detection when tally exceeds ceiling."""
        # Set daily_tokens ceiling to 10,000 and tally to 12,000
        self.db.update_hard_limit(
            limit_type="daily_tokens",
            ceiling_value=10_000.0,
            reset_interval_seconds=86400,
            action_type="block_and_alert",
            is_active=True,
        )
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE hard_limits SET current_tally = 12000.0 WHERE limit_type = 'daily_tokens'"
            )

        breaches = self.manager.check_and_evaluate()
        self.assertGreaterEqual(len(breaches), 1)

        b = next(item for item in breaches if item["limit_type"] == "daily_tokens")
        self.assertEqual(b["ceiling_value"], 10_000.0)
        self.assertEqual(b["current_tally"], 12_000.0)
        self.assertEqual(b["percentage"], 120.0)
        self.assertEqual(b["action_type"], "block_and_alert")

        # Confirm network proxy block is activated
        self.assertTrue(self.manager.is_network_blocked())

    def test_cli_warning_banner_generation(self):
        """Verify writing warning banner file upon kill_cli action."""
        breach = {
            "limit_type": "hourly_tokens",
            "ceiling_value": 50000.0,
            "current_tally": 52000.0,
            "action_type": "kill_cli",
        }
        self.manager._write_cli_warning_banner(breach, [99999])

        from claude_sentinel.quota_manager import WARNING_FILE
        self.assertTrue(WARNING_FILE.exists())
        content = WARNING_FILE.read_text(encoding="utf-8")
        self.assertIn("CLAUDE SENTINEL QUOTA ALERT", content)
        self.assertIn("hourly_tokens", content)


if __name__ == "__main__":
    unittest.main()
