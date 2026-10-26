"""Unit tests for API connection gating, locked telemetry screen, and action guard."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from PyQt6 import QtWidgets

from claude_sentinel.database import DatabaseManager
from claude_sentinel.gui import SentinelDashboard


class TestApiGating(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.temp_dir.name, "test_gating.db")
        self.db = DatabaseManager(self.db_path)
        self.dashboard = SentinelDashboard(self.db)

    def tearDown(self):
        self.dashboard._cleanup_workers()
        self.db.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_default_unauthenticated_state_is_locked(self):
        """Verify that before connecting API, software is locked and graphs are hidden."""
        self.assertFalse(self.dashboard._check_api_connected())

        # Telemetry stack is at index 0 (LockedBanner view)
        self.assertEqual(self.dashboard.telemetry_stack.currentIndex(), 0)

        # Header badge says API Disconnected
        self.assertIn("API Disconnected", self.dashboard.badge_model.text())

    @patch("PyQt6.QtWidgets.QMessageBox.warning")
    def test_guard_api_action_blocks_unauthorized_attempts(self, mock_warning):
        """Verify that attempting to perform actions without API connection is blocked."""
        # Action guard must return False and switch to Account tab (index 3)
        res = self.dashboard._guard_api_action("Update Hard Limit")
        self.assertFalse(res)
        self.assertEqual(self.dashboard.tabs.currentIndex(), 3)
        mock_warning.assert_called_once()

    @patch("PyQt6.QtWidgets.QMessageBox.warning")
    def test_unlock_telemetry_upon_successful_api_connection(self, mock_warning):
        """Verify that when API connection is authenticated, graphs and controls unlock."""
        self.dashboard.auth_mgr.save_credentials(
            api_key="sk-ant-demo",
            validate=True,
        )
        self.dashboard._apply_connection_state(is_connected=True)

        self.assertTrue(self.dashboard._check_api_connected())

        # Telemetry stack is now at index 1 (Unlocked graphs view)
        self.assertEqual(self.dashboard.telemetry_stack.currentIndex(), 1)

        # Header badge indicates model tracking is active
        self.assertIn("Auto-Detecting", self.dashboard.badge_model.text())

        # Action guard now permits actions without opening warning dialog
        self.assertTrue(self.dashboard._guard_api_action("Update Hard Limit"))
        mock_warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
