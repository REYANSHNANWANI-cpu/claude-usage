"""Contextual Activity Engine: 2-second polling worker classifying user interface state."""
import logging
import re
import threading
import time
from typing import Callable, Dict, List, Optional, Set, Tuple

try:
    import psutil
except ImportError:
    psutil = None

from .database import DatabaseManager
from .window_detector import get_active_window_info

logger = logging.getLogger("claude_sentinel.activity_tracker")

# Browser process names (lowercase)
BROWSER_PROCESSES: Set[str] = {
    "chrome.exe",
    "chrome",
    "msedge.exe",
    "msedge",
    "firefox.exe",
    "firefox",
    "safari",
    "brave.exe",
    "brave",
    "opera.exe",
    "opera",
    "arc.exe",
    "arc",
    "vivaldi.exe",
    "vivaldi",
}

# Terminal & IDE process names
TERMINAL_PROCESSES: Set[str] = {
    "powershell.exe",
    "pwsh.exe",
    "cmd.exe",
    "windowsterminal.exe",
    "conhost.exe",
    "bash.exe",
    "wsl.exe",
    "iterm2",
    "terminal",
    "alacritty",
    "code.exe",
    "code",
    "cursor.exe",
    "cursor",
    "hyper.exe",
    "hyper",
    "claude.exe",
    "claude",
}

# Regular expressions matching Claude Web URLs / titles
CLAUDE_WEB_REGEX = re.compile(
    r"(claude\.ai/chat|claude\.ai|claude\s*-\s*chat|\bclaude\b.*anthropic|claude\b)",
    re.IGNORECASE,
)

# Regular expressions matching Claude CLI in window title
CLAUDE_CLI_REGEX = re.compile(
    r"(\bclaude\b|\bclaude-code\b|claude\.exe)",
    re.IGNORECASE,
)


def is_claude_cli_running() -> Tuple[bool, Optional[int]]:
    """Scan running processes via psutil to see if claude CLI is executing."""
    if not psutil:
        return False, None

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            if name in ("claude.exe", "claude"):
                return True, proc.info["pid"]

            cmdline = proc.info.get("cmdline") or []
            cmd_str = " ".join(cmdline).lower()
            if "claude" in cmd_str and any(t in name for t in ("node", "python", "cmd", "pwsh", "powershell")):
                # Check for cli execution arguments
                if any(k in cmd_str for k in ("@anthropic-ai/claude-code", "bin/claude", "\\claude")):
                    return True, proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return False, None


def classify_interface(
    process_name: str,
    window_title: str,
    pid: Optional[int] = None,
) -> Tuple[str, str]:
    """
    Classify the current active user context.
    Returns: (interface_type, human_readable_state)
    Where interface_type is in:
      - 'claude_chat'  -> "Claude Web Chatting"
      - 'claude_code'  -> "Claude Code (CLI Execution)"
      - 'idle_other'   -> "Idle/Other Work"
    """
    proc_lower = (process_name or "").lower()
    title_lower = (window_title or "").lower()

    # 1. Check for Claude Web in Browsers
    if proc_lower in BROWSER_PROCESSES or any(b in proc_lower for b in ("chrome", "edge", "firefox", "safari")):
        if CLAUDE_WEB_REGEX.search(title_lower):
            return "claude_chat", "Claude Web Chatting"

    # Also match direct browser title if process is empty or generic
    if any(k in title_lower for k in ("claude.ai", "claude chat", "claude | anthropic")):
        return "claude_chat", "Claude Web Chatting"

    # 2. Check for Claude Code (CLI / Terminal)
    is_terminal = (
        proc_lower in TERMINAL_PROCESSES
        or any(t in proc_lower for t in ("powershell", "cmd", "terminal", "bash", "code", "cursor"))
        or "claude" in proc_lower
    )

    cli_running, _ = is_claude_cli_running()

    if is_terminal and (CLAUDE_CLI_REGEX.search(title_lower) or cli_running):
        return "claude_code", "Claude Code (CLI Execution)"

    # Direct match if process is claude.exe
    if "claude" in proc_lower:
        return "claude_code", "Claude Code (CLI Execution)"

    # 3. Default to Idle / Other Work
    return "idle_other", "Idle/Other Work"


class ActivityTracker(threading.Thread):
    """
    Background worker polling active foreground window and process every 2 seconds.
    Logs telemetry snapshots to SQLite and notifies listeners.
    """

    def __init__(
        self,
        db: DatabaseManager,
        poll_interval: float = 2.0,
        on_state_change: Optional[Callable[[Dict[str, any]], None]] = None,
        on_tick: Optional[Callable[[Dict[str, any]], None]] = None,
    ):
        super().__init__(name="ActivityTrackerThread", daemon=True)
        self.db = db
        self.poll_interval = poll_interval
        self.on_state_change = on_state_change
        self.on_tick = on_tick
        self._running = False
        self._last_interface = "idle_other"

    def run(self) -> None:
        """Main polling loop running every 2 seconds."""
        self._running = True
        logger.info("ActivityTracker daemon started (polling interval: %ss)", self.poll_interval)

        while self._running:
            try:
                info = get_active_window_info()
                title = info.get("title") or ""
                proc = info.get("process_name") or ""
                pid = info.get("pid")

                interface_type, readable_label = classify_interface(proc, title, pid)

                # Persist to database
                self.db.log_activity(
                    process_name=proc or "unknown",
                    window_title=title or "",
                    interface_type=interface_type,
                    duration_seconds=self.poll_interval,
                )

                payload = {
                    "timestamp": time.time(),
                    "process_name": proc,
                    "window_title": title,
                    "pid": pid,
                    "interface_type": interface_type,
                    "readable_label": readable_label,
                }

                # Trigger tick callback
                if self.on_tick:
                    try:
                        self.on_tick(payload)
                    except Exception as e:
                        logger.error("Error in on_tick callback: %s", e)

                # Trigger state change callback if interface shifted
                if interface_type != self._last_interface:
                    logger.info("State changed: %s -> %s", self._last_interface, interface_type)
                    self._last_interface = interface_type
                    if self.on_state_change:
                        try:
                            self.on_state_change(payload)
                        except Exception as e:
                            logger.error("Error in on_state_change callback: %s", e)

            except Exception as e:
                logger.error("Unexpected error in ActivityTracker loop: %s", e)

            time.sleep(self.poll_interval)

    def stop(self) -> None:
        """Signal tracker thread to terminate."""
        self._running = False
