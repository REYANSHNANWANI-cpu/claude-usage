"""Unit tests for Token Monitor, Pricing Calculator, and JSONL log parser."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from claude_sentinel.database import DatabaseManager
from claude_sentinel.pricing import calculate_cost_usd, get_model_pricing
from claude_sentinel.token_monitor import TokenMonitor


class TestTokenMonitorAndPricing(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.temp_dir.name, "test_tokens.db")
        self.db = DatabaseManager(self.db_path)
        self.projects_dir = Path(self.temp_dir.name) / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.db.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_pricing_calculator(self):
        """Test pricing rates and cost calculation including cache discounts."""
        # Sonnet 3.5: $3.00/M in, $15.00/M out, $3.75/M cache write, $0.30/M cache read
        cost = calculate_cost_usd(
            model_name="claude-3-5-sonnet-20241022",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_creation_tokens=1_000_000,
            cache_read_tokens=1_000_000,
        )
        expected = 3.00 + 15.00 + 3.75 + 0.30  # $22.05
        self.assertAlmostEqual(cost, expected, places=4)

        # Haiku 3.5
        cost_haiku = calculate_cost_usd(
            model_name="claude-3-5-haiku-20241022",
            input_tokens=100_000,
            output_tokens=50_000,
        )
        # (100k/1M)*0.8 + (50k/1M)*4.0 = 0.08 + 0.20 = 0.28
        self.assertAlmostEqual(cost_haiku, 0.28, places=4)

    def test_token_monitor_ingest_and_event_callback(self):
        """Verify direct token ingestion records into database and triggers callback."""
        received = []

        def on_metric(m):
            received.append(m)

        monitor = TokenMonitor(
            db=self.db,
            on_metric_recorded=on_metric,
            projects_dir=self.projects_dir,
        )

        res = monitor.ingest_token_metric(
            source_env="claude_code",
            model_name="claude-3-5-sonnet-20241022",
            input_tokens=5000,
            output_tokens=1200,
            cache_creation_tokens=500,
            cache_read_tokens=1500,
            session_id="test-session-uuid",
        )

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["total_tokens"], 5000 + 1200 + 500 + 1500)
        self.assertEqual(received[0]["source_env"], "claude_code")
        self.assertGreater(received[0]["estimated_cost_usd"], 0.0)

    def test_jsonl_log_line_parsing(self):
        """Verify real Claude Code JSONL log format is correctly parsed."""
        records = []

        def on_metric(m):
            records.append(m)

        monitor = TokenMonitor(
            db=self.db,
            on_metric_recorded=on_metric,
            projects_dir=self.projects_dir,
        )

        # Simulate Claude Code assistant line
        sample_line = json.dumps({
            "parentUuid": "parent-1",
            "type": "assistant",
            "uuid": "sample-uuid-001",
            "timestamp": "2026-10-25T16:00:00.000Z",
            "sessionId": "session-xyz",
            "message": {
                "id": "msg-001",
                "model": "claude-opus-5[1m]",
                "role": "assistant",
                "usage": {
                    "input_tokens": 12500,
                    "output_tokens": 640,
                    "cache_creation_input_tokens": 1000,
                    "cache_read_input_tokens": 3000,
                },
            },
        })

        monitor._parse_log_line(sample_line, "sample.jsonl")

        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["model_name"], "claude-opus-5[1m]")
        self.assertEqual(rec["input_tokens"], 12500)
        self.assertEqual(rec["output_tokens"], 640)
        self.assertEqual(rec["cache_creation_tokens"], 1000)
        self.assertEqual(rec["cache_read_tokens"], 3000)
        self.assertEqual(rec["total_tokens"], 12500 + 640 + 1000 + 3000)

        # Re-parsing same line should be deduplicated by UUID
        monitor._parse_log_line(sample_line, "sample.jsonl")
        self.assertEqual(len(records), 1)


if __name__ == "__main__":
    unittest.main()
