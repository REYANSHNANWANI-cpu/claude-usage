"""Token Metric & Model Extractor: Tails Claude Code session logs and parses real-time telemetry."""
import glob
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from .database import DatabaseManager
from .pricing import calculate_cost_usd

logger = logging.getLogger("claude_sentinel.token_monitor")

CLAUDE_HOME = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_HOME / "projects"


class TokenMonitor(threading.Thread):
    """
    Background worker monitoring Claude Code project session logs (*.jsonl)
    and ingesting exact token metrics and model IDs.
    """

    def __init__(
        self,
        db: DatabaseManager,
        poll_interval: float = 1.5,
        on_metric_recorded: Optional[Callable[[Dict[str, Any]], None]] = None,
        projects_dir: Optional[Path] = None,
    ):
        super().__init__(name="TokenMonitorThread", daemon=True)
        self.db = db
        self.poll_interval = poll_interval
        self.on_metric_recorded = on_metric_recorded
        self.projects_dir = projects_dir or PROJECTS_DIR
        self._running = False
        self._file_offsets: Dict[str, int] = {}
        self._processed_uuids: Set[str] = set()
        self._last_known_model: str = "claude-3-5-sonnet-20241022"

    def run(self) -> None:
        """Scan and tail project session JSONL files."""
        self._running = True
        logger.info("TokenMonitor daemon started (projects dir: %s)", self.projects_dir)

        # Initial pass: initialize offsets to current EOF for existing historical files
        # so we only process new real-time tokens created during this session
        self._initialize_offsets()

        while self._running:
            try:
                self._poll_session_files()
            except Exception as e:
                logger.error("Error in TokenMonitor poll cycle: %s", e)

            time.sleep(self.poll_interval)

    def stop(self) -> None:
        """Signal token monitor to terminate."""
        self._running = False

    def _initialize_offsets(self) -> None:
        """Seek existing files to end of file to establish baseline."""
        if not self.projects_dir.exists():
            return

        pattern = str(self.projects_dir / "**" / "*.jsonl")
        for file_path in glob.glob(pattern, recursive=True):
            try:
                size = os.path.getsize(file_path)
                self._file_offsets[file_path] = size
            except OSError:
                pass

    def _poll_session_files(self) -> None:
        """Find recently modified jsonl files and parse new lines."""
        if not self.projects_dir.exists():
            return

        pattern = str(self.projects_dir / "**" / "*.jsonl")
        for file_path in glob.glob(pattern, recursive=True):
            try:
                current_size = os.path.getsize(file_path)
                last_offset = self._file_offsets.get(file_path, 0)

                if current_size > last_offset:
                    self._read_new_lines(file_path, last_offset, current_size)
                    self._file_offsets[file_path] = current_size
                elif current_size < last_offset:
                    # File was truncated or rotated
                    self._file_offsets[file_path] = 0
            except OSError as e:
                logger.debug("Failed reading file %s: %s", file_path, e)

    def _read_new_lines(self, file_path: str, start_offset: int, end_offset: int) -> None:
        """Read bytes from start_offset to end_offset and parse JSON records."""
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(start_offset)
                while f.tell() < end_offset:
                    line = f.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    self._parse_log_line(line, file_path)
        except Exception as e:
            logger.error("Error tailing %s: %s", file_path, e)

    def _parse_log_line(self, line: str, file_path: str) -> None:
        """Parse a single JSONL event line from Claude Code logs."""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return

        event_uuid = data.get("uuid") or data.get("leafUuid") or data.get("parentUuid")
        if event_uuid and event_uuid in self._processed_uuids:
            return

        # Check for model announcement attachment
        attachment = data.get("attachment")
        if isinstance(attachment, dict):
            identity = attachment.get("identity")
            if isinstance(identity, dict) and "modelId" in identity:
                self._last_known_model = identity["modelId"]

        # Parse assistant message usage
        msg = data.get("message")
        if not isinstance(msg, dict):
            return

        # Model from message
        model = msg.get("model") or self._last_known_model
        if model and model != "<synthetic>":
            self._last_known_model = model
        else:
            model = self._last_known_model

        usage = msg.get("usage")
        if not isinstance(usage, dict):
            return

        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        cache_creation = int(usage.get("cache_creation_input_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        total_tokens = input_tokens + output_tokens + cache_creation + cache_read

        # If all tokens are zero, ignore synthetic/error lines
        if total_tokens == 0:
            return

        if event_uuid:
            self._processed_uuids.add(event_uuid)

        session_id = data.get("sessionId") or data.get("session_id") or Path(file_path).stem
        timestamp = data.get("timestamp")

        # Ingest metrics
        self.ingest_token_metric(
            source_env="claude_code",
            model_name=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
            total_tokens=total_tokens,
            session_id=session_id,
            timestamp_iso=timestamp,
        )

    def ingest_token_metric(
        self,
        source_env: str,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
        total_tokens: Optional[int] = None,
        session_id: Optional[str] = None,
        timestamp_iso: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Record and calculate token consumption metric into database.
        Trigger registered listener callbacks.
        """
        if total_tokens is None:
            total_tokens = input_tokens + output_tokens + cache_creation_tokens + cache_read_tokens

        cost = calculate_cost_usd(
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
        )

        metric_id = self.db.record_token_metric(
            source_env=source_env,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=cost,
            session_id=session_id,
            timestamp_iso=timestamp_iso,
        )

        payload = {
            "id": metric_id,
            "source_env": source_env,
            "model_name": model_name,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "cache_read_tokens": cache_read_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": cost,
            "session_id": session_id,
            "timestamp": timestamp_iso or time.time(),
        }

        logger.info(
            "Ingested token metric #%s: %s | %s tokens (%s in, %s out) | $%.4f",
            metric_id,
            model_name,
            total_tokens,
            input_tokens,
            output_tokens,
            cost,
        )

        if self.on_metric_recorded:
            try:
                self.on_metric_recorded(payload)
            except Exception as e:
                logger.error("Error in on_metric_recorded callback: %s", e)

        return payload
