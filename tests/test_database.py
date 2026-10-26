"""Unit tests for SQLite database manager, schema migrations, and CRUD operations."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from claude_sentinel.database import DatabaseManager


class TestDatabaseManager(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.temp_dir.name, "test_sentinel.db")
        self.db = DatabaseManager(self.db_path)

    def tearDown(self):
        self.db.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_schema_initialization_and_defaults(self):
        """Verify all tables exist and default hard limits are seeded."""
        limits = self.db.get_hard_limits()
        self.assertGreaterEqual(len(limits), 4)

        limit_types = [l["limit_type"] for l in limits]
        self.assertIn("hourly_tokens", limit_types)
        self.assertIn("daily_tokens", limit_types)
        self.assertIn("daily_cost_usd", limit_types)
        self.assertIn("hourly_calls", limit_types)

    def test_session_config_crud(self):
        """Verify saving and retrieving encrypted session credentials."""
        self.db.save_session_config(
            api_key_encrypted="enc_key_123",
            session_token_encrypted="enc_tok_abc",
            is_authenticated=True,
        )

        config = self.db.get_session_config()
        self.assertIsNotNone(config)
        self.assertEqual(config["api_key_encrypted"], "enc_key_123")
        self.assertEqual(config["session_token_encrypted"], "enc_tok_abc")
        self.assertEqual(config["is_authenticated"], 1)

    def test_activity_logging(self):
        """Verify activity logs are inserted and recent queries return results."""
        row_id = self.db.log_activity(
            process_name="chrome.exe",
            window_title="Claude - Chat - Google Chrome",
            interface_type="claude_chat",
            duration_seconds=2.0,
        )
        self.assertIsInstance(row_id, int)

        recent = self.db.get_recent_activities(limit=5)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["interface_type"], "claude_chat")
        self.assertEqual(recent[0]["process_name"], "chrome.exe")

    def test_token_metrics_and_limit_tally_updates(self):
        """Verify recording token metrics increments hard limit tallies atomically."""
        # Check initial tally
        limits = {l["limit_type"]: l["current_tally"] for l in self.db.get_hard_limits()}
        initial_daily = limits["daily_tokens"]
        initial_cost = limits["daily_cost_usd"]

        metric_id = self.db.record_token_metric(
            source_env="claude_code",
            model_name="claude-3-5-sonnet-20241022",
            input_tokens=1000,
            output_tokens=500,
            cache_creation_tokens=200,
            cache_read_tokens=100,
            total_tokens=1800,
            estimated_cost_usd=0.015,
            session_id="test-session-1",
        )
        self.assertIsInstance(metric_id, int)

        # Check updated tally
        updated_limits = {l["limit_type"]: l["current_tally"] for l in self.db.get_hard_limits()}
        self.assertEqual(updated_limits["daily_tokens"], initial_daily + 1800)
        self.assertAlmostEqual(updated_limits["daily_cost_usd"], initial_cost + 0.015, places=5)
        self.assertEqual(updated_limits["hourly_calls"], 1.0)

        # Summary for today
        summary = self.db.get_token_summary_today()
        self.assertEqual(summary["total_tokens"], 1800)
        self.assertEqual(summary["input_tokens"], 1000)
        self.assertEqual(summary["output_tokens"], 500)
        self.assertEqual(summary["cache_creation_tokens"], 200)
        self.assertEqual(summary["cache_read_tokens"], 100)
        self.assertAlmostEqual(summary["estimated_cost_usd"], 0.015, places=5)

    def test_hard_limits_check_and_reset(self):
        """Verify that tallies are reset when reset_interval_seconds has elapsed."""
        # Set tally and set last_reset_timestamp to 2 hours ago
        past_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE hard_limits
                SET current_tally = 5000,
                    reset_interval_seconds = 3600,
                    last_reset_timestamp = ?
                WHERE limit_type = 'hourly_tokens'
                """,
                (past_time,),
            )

        reset_types = self.db.check_and_reset_limits()
        self.assertIn("hourly_tokens", reset_types)

        # Confirm tally reset to 0
        limits = {l["limit_type"]: l["current_tally"] for l in self.db.get_hard_limits()}
        self.assertEqual(limits["hourly_tokens"], 0.0)


if __name__ == "__main__":
    unittest.main()
