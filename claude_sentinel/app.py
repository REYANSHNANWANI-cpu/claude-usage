"""Application lifecycle manager supporting GUI and headless daemon modes."""
import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

from .activity_tracker import ActivityTracker
from .database import DatabaseManager
from .network_proxy import NetworkProxyDaemon
from .pricing import calculate_cost_usd
from .quota_manager import QuotaManager
from .token_monitor import TokenMonitor

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("claude_sentinel")


def run_gui(db_path: Optional[str] = None) -> None:
    """Launch the PyQt6 administrative dashboard and embedded daemons."""
    from PyQt6 import QtWidgets
    from .gui import SentinelDashboard

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Claude Sentinel")
    app.setOrganizationName("Anthropic Tools")

    db = DatabaseManager(db_path)
    dashboard = SentinelDashboard(db)
    dashboard.show()

    sys.exit(app.exec())


def run_headless(db_path: Optional[str] = None, run_duration_sec: Optional[float] = None) -> None:
    """Run pure background daemons (ActivityTracker, TokenMonitor, QuotaManager, Proxy)."""
    logger.info("Initializing Claude Sentinel Headless Daemon...")
    db = DatabaseManager(db_path)

    quota_mgr = QuotaManager(
        db=db,
        on_quota_breach=lambda b: logger.warning("HEADLESS ALERT: Quota Breached: %s", b),
        on_action_executed=lambda a: logger.warning("HEADLESS ACTION: %s", a),
    )

    activity_tracker = ActivityTracker(
        db=db,
        poll_interval=2.0,
        on_state_change=lambda s: logger.info("STATE CHANGE: %s (%s)", s["readable_label"], s["process_name"]),
    )

    token_monitor = TokenMonitor(
        db=db,
        poll_interval=1.5,
        on_metric_recorded=lambda m: logger.info(
            "TOKEN METRIC: %s | %s tokens | $%.4f",
            m["model_name"],
            m["total_tokens"],
            m["estimated_cost_usd"],
        ),
    )

    proxy_daemon = NetworkProxyDaemon(
        quota_manager=quota_mgr,
        token_monitor=token_monitor,
    )

    activity_tracker.start()
    token_monitor.start()
    proxy_daemon.start()

    logger.info("All Claude Sentinel background daemons active. Press Ctrl+C to stop.")

    start_time = time.time()
    try:
        while True:
            # Evaluate quotas
            quota_mgr.check_and_evaluate()
            time.sleep(2.5)

            if run_duration_sec and (time.time() - start_time) >= run_duration_sec:
                logger.info("Requested run duration (%.1fs) reached. Exiting cleanly.", run_duration_sec)
                break
    except KeyboardInterrupt:
        logger.info("Shutdown signal received.")
    finally:
        logger.info("Stopping daemons...")
        activity_tracker.stop()
        token_monitor.stop()
        proxy_daemon.stop()
        logger.info("Claude Sentinel daemons terminated cleanly.")


def main() -> None:
    """CLI entry point parsing command line arguments."""
    parser = argparse.ArgumentParser(description="Claude Sentinel - Telemetry & Quota Enforcement Daemon")
    parser.add_argument("--gui", action="store_true", default=True, help="Launch administrative GUI (default)")
    parser.add_argument("--headless", action="store_true", help="Run background daemon without GUI")
    parser.add_argument("--test-headless", action="store_true", help="Run brief automated headless test and exit")
    parser.add_argument("--db-path", type=str, default=None, help="Custom SQLite database file path")

    args = parser.parse_args()

    if args.test_headless:
        # Run headless for 6 seconds as a functional verification test
        run_headless(db_path=args.db_path, run_duration_sec=6.0)
    elif args.headless:
        run_headless(db_path=args.db_path)
    else:
        run_gui(db_path=args.db_path)


if __name__ == "__main__":
    main()
