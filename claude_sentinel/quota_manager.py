"""Hard Limit & Quota Enforcement: Evaluates usage ceilings and executes throttling/alerts."""
import logging
import os
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import psutil

from .database import DatabaseManager

logger = logging.getLogger("claude_sentinel.quota_manager")

CLAUDE_HOME = Path.home() / ".claude"
WARNING_FILE = CLAUDE_HOME / "LIMIT_EXCEEDED_WARNING.txt"


class QuotaManager:
    """
    Evaluates usage against user-configured ceilings (tokens/hr, tokens/day, cost/day).
    Enforces actions: Kill/Pause Claude Code CLI and trigger Web Alert/Blocking.
    """

    def __init__(
        self,
        db: DatabaseManager,
        on_quota_breach: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_action_executed: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.db = db
        self.on_quota_breach = on_quota_breach
        self.on_action_executed = on_action_executed
        self._network_blocked: bool = False
        self._last_breach_times: Dict[str, float] = {}

    def check_and_evaluate(self) -> List[Dict[str, Any]]:
        """
        Cycle check: reset expired limits, evaluate current tallies,
        and trigger enforcement if limits are crossed.
        """
        # 1. Reset expired tallies
        reset_types = self.db.check_and_reset_limits()
        if reset_types:
            logger.info("Reset expired limits: %s", reset_types)
            # If blocking limits were reset, unblock network
            if any(t in reset_types for t in ("daily_tokens", "hourly_tokens", "daily_cost_usd")):
                self._network_blocked = False

        # 2. Fetch current active limits
        limits = self.db.get_hard_limits()
        breaches = []

        for limit in limits:
            if not limit["is_active"]:
                continue

            ceiling = float(limit["ceiling_value"])
            tally = float(limit["current_tally"])
            limit_type = limit["limit_type"]
            action = limit.get("action_type", "warn_only")

            if tally >= ceiling:
                breach_info = {
                    "limit_type": limit_type,
                    "ceiling_value": ceiling,
                    "current_tally": tally,
                    "action_type": action,
                    "percentage": round((tally / ceiling) * 100, 1),
                    "timestamp": time.time(),
                }
                breaches.append(breach_info)

                # Rate-limit enforcement triggers (every 5 seconds)
                last_time = self._last_breach_times.get(limit_type, 0)
                if time.time() - last_time > 5.0:
                    self._last_breach_times[limit_type] = time.time()
                    self._execute_enforcement(breach_info)

        return breaches

    def is_network_blocked(self) -> bool:
        """Query whether Claude Web requests should be blocked at proxy gate."""
        return self._network_blocked

    def set_network_blocked(self, blocked: bool) -> None:
        """Manually toggle proxy block state."""
        self._network_blocked = blocked

    def _execute_enforcement(self, breach: Dict[str, Any]) -> None:
        """Execute configured action for the breached quota."""
        action = breach["action_type"]
        limit_type = breach["limit_type"]
        tally = breach["current_tally"]
        ceiling = breach["ceiling_value"]

        logger.warning(
            "QUOTA BREACH DETECTED: %s reached %.1f (Ceiling: %.1f). Action: %s",
            limit_type,
            tally,
            ceiling,
            action,
        )

        if self.on_quota_breach:
            try:
                self.on_quota_breach(breach)
            except Exception as e:
                logger.error("Error in on_quota_breach handler: %s", e)

        # Action: Warn only
        if action == "warn_only":
            self._notify_user(breach, "Warning: Usage threshold exceeded.")

        # Action: Block and alert (Claude Chat)
        elif action == "block_and_alert":
            self._network_blocked = True
            self._notify_user(breach, "Network routing throttled. Quota ceiling exceeded.")

        # Action: Kill or pause Claude Code CLI
        elif action == "kill_cli":
            self._network_blocked = True
            killed_pids = self.terminate_claude_cli_processes(pause_instead=False)
            self._write_cli_warning_banner(breach, killed_pids)
            self._notify_user(
                breach,
                f"Claude Code process terminated. (PIDs: {killed_pids}). Quota ceiling exceeded.",
            )

        if self.on_action_executed:
            try:
                self.on_action_executed({
                    "action": action,
                    "breach": breach,
                    "timestamp": time.time(),
                })
            except Exception as e:
                logger.error("Error in on_action_executed handler: %s", e)

    def terminate_claude_cli_processes(self, pause_instead: bool = False) -> List[int]:
        """
        Locate and terminate (or suspend) running Claude CLI sub-processes.
        Returns list of targeted PIDs.
        """
        targeted_pids = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info["name"] or "").lower()
                cmdline = proc.info.get("cmdline") or []
                cmd_str = " ".join(cmdline).lower()

                is_target = False
                if name in ("claude.exe", "claude"):
                    is_target = True
                elif "claude" in cmd_str and any(t in name for t in ("node", "python", "cmd", "pwsh")):
                    if any(k in cmd_str for k in ("@anthropic-ai/claude-code", "bin/claude", "\\claude")):
                        is_target = True

                if is_target:
                    pid = proc.info["pid"]
                    p = psutil.Process(pid)
                    if pause_instead:
                        p.suspend()
                        logger.warning("Suspended Claude Code process PID %s", pid)
                    else:
                        p.terminate()
                        logger.warning("Terminated Claude Code process PID %s", pid)
                    targeted_pids.append(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return targeted_pids

    def _write_cli_warning_banner(self, breach: Dict[str, Any], pids: List[int]) -> None:
        """Write an overt warning banner into Claude Code's home directory."""
        try:
            CLAUDE_HOME.mkdir(parents=True, exist_ok=True)
            banner = f"""
================================================================================
                    *** CLAUDE SENTINEL QUOTA ALERT ***
================================================================================
Timestamp: {datetime.now(timezone.utc).isoformat()}
Enforcement: Allotted Limit Exceeded
Breached Limit: {breach['limit_type']}
Current Tally: {breach['current_tally']:,.0f} tokens / metric units
Limit Ceiling: {breach['ceiling_value']:,.0f}
Targeted Processes: {pids}

Action Taken: Claude Code CLI sub-process was terminated automatically
to prevent unbudgeted cost overruns.

To adjust or reset limits, open the Claude Sentinel administrative dashboard.
================================================================================
"""
            WARNING_FILE.write_text(banner, encoding="utf-8")
        except Exception as e:
            logger.error("Failed writing warning banner to %s: %s", WARNING_FILE, e)

    def _notify_user(self, breach: Dict[str, Any], message: str) -> None:
        """Log and prepare desktop notification."""
        logger.warning("ALERT: %s | %s", message, breach)
