"""SQLite3 database engine, schema management, and CRUD operations for Claude Sentinel."""
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_DB_DIR = Path.home() / ".claude_sentinel"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "sentinel.db"

_lock = threading.RLock()


class DatabaseManager:
    """Thread-safe SQLite manager with WAL mode and connection pooling per thread."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self.db_path = Path(db_path)
        else:
            DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
            self.db_path = DEFAULT_DB_PATH

        self._local = threading.local()
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Retrieve thread-local SQLite connection with dictionary row access."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=15.0,
                check_same_thread=False,
                isolation_level=None,  # Autocommit mode handled by contextmanager
            )
            conn.row_factory = sqlite3.Row
            # Enable WAL mode for high concurrency
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self):
        """Transaction context manager ensuring commit on success and rollback on failure."""
        conn = self.get_connection()
        with _lock:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                yield conn
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def close(self) -> None:
        """Close thread-local database connection."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def init_db(self) -> None:
        """Create database tables, indexes, and seed default limits if absent."""
        conn = self.get_connection()
        with _lock:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS session_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    api_key_encrypted TEXT,
                    session_token_encrypted TEXT,
                    is_authenticated INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS activity_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT DEFAULT (datetime('now')),
                    process_name TEXT NOT NULL,
                    window_title TEXT,
                    interface_type TEXT NOT NULL, -- 'claude_chat', 'claude_code', 'idle_other'
                    duration_seconds REAL DEFAULT 2.0
                );

                CREATE TABLE IF NOT EXISTS claude_token_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT DEFAULT (datetime('now')),
                    session_id TEXT,
                    source_env TEXT NOT NULL, -- 'claude_code', 'claude_chat'
                    model_name TEXT NOT NULL,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cache_creation_tokens INTEGER DEFAULT 0,
                    cache_read_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    estimated_cost_usd REAL DEFAULT 0.0
                );

                CREATE TABLE IF NOT EXISTS hard_limits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    limit_type TEXT UNIQUE NOT NULL, -- 'hourly_tokens', 'daily_tokens', 'daily_cost_usd', 'hourly_calls'
                    ceiling_value REAL NOT NULL,
                    current_tally REAL DEFAULT 0.0,
                    reset_interval_seconds INTEGER DEFAULT 3600,
                    last_reset_timestamp TEXT DEFAULT (datetime('now')),
                    is_active INTEGER DEFAULT 1,
                    action_type TEXT DEFAULT 'warn_only' -- 'block_and_alert', 'kill_cli', 'warn_only'
                );

                CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity_logs (timestamp);
                CREATE INDEX IF NOT EXISTS idx_activity_interface ON activity_logs (interface_type);
                CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON claude_token_metrics (timestamp);
                CREATE INDEX IF NOT EXISTS idx_metrics_source ON claude_token_metrics (source_env);
                CREATE INDEX IF NOT EXISTS idx_limits_type ON hard_limits (limit_type);
                """
            )

            # Seed default limits if empty
            cursor = conn.execute("SELECT COUNT(*) as count FROM hard_limits")
            row = cursor.fetchone()
            if row["count"] == 0:
                defaults = [
                    ("hourly_tokens", 500_000.0, 3600, "warn_only", 1),
                    ("daily_tokens", 3_000_000.0, 86400, "block_and_alert", 1),
                    ("daily_cost_usd", 25.0, 86400, "kill_cli", 1),
                    ("hourly_calls", 150.0, 3600, "warn_only", 1),
                ]
                now_iso = datetime.now(timezone.utc).isoformat()
                conn.executemany(
                    """
                    INSERT INTO hard_limits (limit_type, ceiling_value, reset_interval_seconds, action_type, is_active, last_reset_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [(d[0], d[1], d[2], d[3], d[4], now_iso) for d in defaults],
                )

    # ------------------ Session Config CRUD ------------------

    def save_session_config(
        self,
        api_key_encrypted: Optional[str],
        session_token_encrypted: Optional[str],
        is_authenticated: bool = False,
    ) -> None:
        """Persist or update encrypted credentials and auth status."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO session_config (id, api_key_encrypted, session_token_encrypted, is_authenticated, created_at, updated_at)
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    api_key_encrypted = COALESCE(excluded.api_key_encrypted, session_config.api_key_encrypted),
                    session_token_encrypted = COALESCE(excluded.session_token_encrypted, session_config.session_token_encrypted),
                    is_authenticated = excluded.is_authenticated,
                    updated_at = excluded.updated_at;
                """,
                (
                    api_key_encrypted,
                    session_token_encrypted,
                    1 if is_authenticated else 0,
                    now_iso,
                    now_iso,
                ),
            )

    def get_session_config(self) -> Optional[Dict[str, Any]]:
        """Fetch current session configuration."""
        conn = self.get_connection()
        cursor = conn.execute("SELECT * FROM session_config WHERE id = 1")
        row = cursor.fetchone()
        return dict(row) if row else None

    # ------------------ Activity Logs CRUD ------------------

    def log_activity(
        self,
        process_name: str,
        window_title: str,
        interface_type: str,
        duration_seconds: float = 2.0,
    ) -> int:
        """Log a foreground activity snapshot."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO activity_logs (timestamp, process_name, window_title, interface_type, duration_seconds)
                VALUES (?, ?, ?, ?, ?)
                """,
                (now_iso, process_name, window_title, interface_type, duration_seconds),
            )
            return cursor.lastrowid

    def get_recent_activities(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent activity log entries."""
        conn = self.get_connection()
        cursor = conn.execute(
            """
            SELECT * FROM activity_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]

    def get_activity_breakdown(self, hours: int = 24) -> Dict[str, float]:
        """Aggregate total seconds spent across interface types in past N hours."""
        conn = self.get_connection()
        cursor = conn.execute(
            """
            SELECT interface_type, SUM(duration_seconds) as total_duration
            FROM activity_logs
            WHERE timestamp >= datetime('now', '-' || ? || ' hours')
            GROUP BY interface_type
            """,
            (hours,),
        )
        results = {"claude_chat": 0.0, "claude_code": 0.0, "idle_other": 0.0}
        for row in cursor.fetchall():
            results[row["interface_type"]] = float(row["total_duration"] or 0.0)
        return results

    # ------------------ Token Metrics CRUD ------------------

    def record_token_metric(
        self,
        source_env: str,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
        total_tokens: Optional[int] = None,
        estimated_cost_usd: float = 0.0,
        session_id: Optional[str] = None,
        timestamp_iso: Optional[str] = None,
    ) -> int:
        """Record an exact token consumption entry and increment limit tallies."""
        if total_tokens is None:
            total_tokens = input_tokens + output_tokens + cache_creation_tokens + cache_read_tokens

        ts = timestamp_iso or datetime.now(timezone.utc).isoformat()

        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO claude_token_metrics (
                    timestamp, session_id, source_env, model_name,
                    input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
                    total_tokens, estimated_cost_usd
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    session_id,
                    source_env,
                    model_name,
                    input_tokens,
                    output_tokens,
                    cache_creation_tokens,
                    cache_read_tokens,
                    total_tokens,
                    estimated_cost_usd,
                ),
            )
            metric_id = cursor.lastrowid

            # Increment active hard limit tallies
            conn.execute(
                "UPDATE hard_limits SET current_tally = current_tally + ? WHERE limit_type IN ('hourly_tokens', 'daily_tokens') AND is_active = 1",
                (total_tokens,),
            )
            conn.execute(
                "UPDATE hard_limits SET current_tally = current_tally + ? WHERE limit_type = 'daily_cost_usd' AND is_active = 1",
                (estimated_cost_usd,),
            )
            conn.execute(
                "UPDATE hard_limits SET current_tally = current_tally + 1 WHERE limit_type = 'hourly_calls' AND is_active = 1",
            )

            return metric_id

    def get_token_summary_today(self) -> Dict[str, Any]:
        """Get total tokens and cost aggregated for today (UTC)."""
        conn = self.get_connection()
        cursor = conn.execute(
            """
            SELECT 
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(input_tokens), 0) as input_tokens,
                COALESCE(SUM(output_tokens), 0) as output_tokens,
                COALESCE(SUM(cache_creation_tokens), 0) as cache_creation_tokens,
                COALESCE(SUM(cache_read_tokens), 0) as cache_read_tokens,
                COALESCE(SUM(estimated_cost_usd), 0.0) as estimated_cost_usd,
                COUNT(*) as call_count
            FROM claude_token_metrics
            WHERE date(timestamp) = date('now')
            """
        )
        row = cursor.fetchone()
        return dict(row) if row else {
            "total_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "estimated_cost_usd": 0.0,
            "call_count": 0,
        }

    def get_burn_rate_series(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Retrieve recent token burn records for real-time charting."""
        conn = self.get_connection()
        cursor = conn.execute(
            """
            SELECT id, timestamp, source_env, model_name, total_tokens, estimated_cost_usd
            FROM claude_token_metrics
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        rows.reverse()  # Return in chronological order
        return rows

    def get_latest_metric(self) -> Optional[Dict[str, Any]]:
        """Get the most recent token metric logged."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT * FROM claude_token_metrics ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    # ------------------ Hard Limits CRUD ------------------

    def get_hard_limits(self) -> List[Dict[str, Any]]:
        """Fetch all configured hard limits."""
        conn = self.get_connection()
        cursor = conn.execute("SELECT * FROM hard_limits ORDER BY id ASC")
        return [dict(r) for r in cursor.fetchall()]

    def update_hard_limit(
        self,
        limit_type: str,
        ceiling_value: float,
        reset_interval_seconds: int = 3600,
        action_type: str = "warn_only",
        is_active: bool = True,
    ) -> None:
        """Update limit ceiling, reset cycle, action mode, and active flag."""
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE hard_limits 
                SET ceiling_value = ?,
                    reset_interval_seconds = ?,
                    action_type = ?,
                    is_active = ?
                WHERE limit_type = ?
                """,
                (
                    ceiling_value,
                    reset_interval_seconds,
                    action_type,
                    1 if is_active else 0,
                    limit_type,
                ),
            )

    def check_and_reset_limits(self) -> List[str]:
        """
        Evaluate all limits and reset tallies whose reset_interval_seconds has elapsed.
        Returns list of limit_types that were reset.
        """
        conn = self.get_connection()
        limits = self.get_hard_limits()
        reset_list = []
        now = datetime.now(timezone.utc)

        for limit in limits:
            last_reset_str = limit["last_reset_timestamp"]
            interval = limit["reset_interval_seconds"]

            try:
                # Handle varying ISO string formats
                last_reset = datetime.fromisoformat(last_reset_str.replace("Z", "+00:00"))
                if last_reset.tzinfo is None:
                    last_reset = last_reset.replace(tzinfo=timezone.utc)
            except Exception:
                last_reset = now

            delta = (now - last_reset).total_seconds()
            if delta >= interval:
                with self.transaction() as trans_conn:
                    trans_conn.execute(
                        """
                        UPDATE hard_limits 
                        SET current_tally = 0.0,
                            last_reset_timestamp = ?
                        WHERE limit_type = ?
                        """,
                        (now.isoformat(), limit["limit_type"]),
                    )
                reset_list.append(limit["limit_type"])

        return reset_list
