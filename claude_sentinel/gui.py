"""PyQt6 Dark-Themed Administrative Dashboard for Claude Sentinel with strict API-connection gating."""
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from .alert_overlay import AlertOverlay
from .auth import AuthManager
from .database import DatabaseManager
from .network_proxy import NetworkProxyDaemon
from .pricing import calculate_cost_usd
from .quota_manager import QuotaManager
from .token_monitor import TokenMonitor
from .window_detector import get_active_window_info

# Enable anti-aliasing in pyqtgraph
pg.setConfigOptions(antialias=True)

# Modern Cyberpunk / Dark Glassmorphism QSS Palette
DARK_STYLESHEET = """
QMainWindow, QDialog, QWidget#CentralWidget {
    background-color: #0b0c10;
    color: #e1e2e6;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}

QTabWidget::pane {
    border: 1px solid #1c2030;
    background-color: #11131c;
    border-radius: 10px;
    margin-top: -1px;
}

QTabBar::tab {
    background-color: #141724;
    color: #7d8396;
    padding: 12px 24px;
    margin-right: 6px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 700;
    font-size: 13px;
    border: 1px solid #1c2030;
    border-bottom: none;
}

QTabBar::tab:selected {
    background-color: #1a1e2e;
    color: #00d2d3;
    border-top: 3px solid #00d2d3;
}

QTabBar::tab:hover {
    color: #ffffff;
    background-color: #181c2b;
}

QFrame.MetricCard {
    background-color: #131622;
    border: 1px solid #1e2336;
    border-radius: 12px;
    padding: 16px;
}

QFrame#LockedBanner {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #181b28, stop:1 #11131c);
    border: 2px dashed #ffa502;
    border-radius: 16px;
    padding: 40px;
}

QLabel.CardTitle {
    color: #798196;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.8px;
    text-transform: uppercase;
}

QLabel.CardValue {
    color: #ffffff;
    font-size: 26px;
    font-weight: 800;
}

QLabel.CardSub {
    color: #555c70;
    font-size: 11px;
}

QProgressBar {
    background-color: #181b29;
    border: 1px solid #23283b;
    border-radius: 8px;
    height: 22px;
    text-align: center;
    color: #ffffff;
    font-weight: bold;
    font-size: 11px;
}

QProgressBar::chunk {
    border-radius: 7px;
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10ac84, stop:1 #1dd1a1);
}

QTableWidget {
    background-color: #12141d;
    border: 1px solid #1c2030;
    border-radius: 10px;
    gridline-color: #1a1d2c;
    color: #dcdde1;
    selection-background-color: #2b3149;
    font-size: 12px;
}

QHeaderView::section {
    background-color: #161925;
    color: #798196;
    padding: 10px 14px;
    border: none;
    border-bottom: 1px solid #222638;
    font-weight: bold;
    font-size: 11px;
    text-transform: uppercase;
}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #141724;
    border: 1px solid #252b40;
    border-radius: 8px;
    color: #f5f6fa;
    padding: 10px 14px;
    font-size: 13px;
}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #00d2d3;
}

QPushButton {
    background-color: #1c2133;
    color: #f5f6fa;
    border: 1px solid #29304a;
    border-radius: 8px;
    padding: 10px 20px;
    font-weight: 700;
    font-size: 13px;
}

QPushButton:hover {
    background-color: #262c45;
    border-color: #3d486e;
}

QPushButton:pressed {
    background-color: #171b2a;
}

QPushButton#PrimaryButton {
    background-color: #10ac84;
    border: 1px solid #1dd1a1;
    color: #ffffff;
}

QPushButton#PrimaryButton:hover {
    background-color: #1dd1a1;
}

QPushButton#GlowingCta {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffa502, stop:1 #ff7f50);
    border: 1px solid #ffa502;
    color: #11131c;
    font-size: 14px;
    font-weight: 800;
    padding: 12px 28px;
    border-radius: 10px;
}

QPushButton#GlowingCta:hover {
    background-color: #ff7f50;
    color: #ffffff;
}

QPushButton#DangerButton {
    background-color: #c23616;
    border: 1px solid #e84118;
    color: #ffffff;
}

QPushButton#DangerButton:hover {
    background-color: #e84118;
}

QScrollBar:vertical {
    background: #0d0e14;
    width: 10px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: #23283c;
    min-height: 20px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #353c5a;
}
"""


# -------------------------------------------------------------
# QThread Workers for Zero UI Latency
# -------------------------------------------------------------

class QActivityWorker(QtCore.QThread):
    """Asynchronous worker for 2s window & process polling."""

    tick_signal = QtCore.pyqtSignal(dict)
    state_changed_signal = QtCore.pyqtSignal(dict)

    def __init__(self, db: DatabaseManager, poll_interval: float = 2.0):
        super().__init__()
        self.db = db
        self.poll_interval = poll_interval
        self._running = True
        self._last_interface = "idle_other"

    def run(self) -> None:
        from .activity_tracker import classify_interface
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

                self.tick_signal.emit(payload)

                if interface_type != self._last_interface:
                    self._last_interface = interface_type
                    self.state_changed_signal.emit(payload)

            except Exception:
                pass

            time.sleep(self.poll_interval)

    def stop(self) -> None:
        self._running = False
        self.wait()


class QTokenWorker(QtCore.QThread):
    """Asynchronous worker for Claude Code log tailing."""

    metric_recorded_signal = QtCore.pyqtSignal(dict)

    def __init__(self, db: DatabaseManager, poll_interval: float = 1.5):
        super().__init__()
        self.db = db
        self.poll_interval = poll_interval
        self._running = True
        self.monitor = TokenMonitor(
            db=self.db,
            poll_interval=self.poll_interval,
            on_metric_recorded=self._on_metric,
        )

    def _on_metric(self, payload: dict) -> None:
        self.metric_recorded_signal.emit(payload)

    def run(self) -> None:
        self.monitor.run()

    def stop(self) -> None:
        self.monitor.stop()
        self._running = False
        self.wait()


class QQuotaWorker(QtCore.QThread):
    """Asynchronous worker periodically checking hard limit ceilings."""

    quota_breach_signal = QtCore.pyqtSignal(dict)
    limits_updated_signal = QtCore.pyqtSignal(list)

    def __init__(self, quota_manager: QuotaManager, interval: float = 3.0):
        super().__init__()
        self.quota_mgr = quota_manager
        self.interval = interval
        self._running = True

    def run(self) -> None:
        while self._running:
            try:
                breaches = self.quota_mgr.check_and_evaluate()
                for b in breaches:
                    self.quota_breach_signal.emit(b)

                limits = self.quota_mgr.db.get_hard_limits()
                self.limits_updated_signal.emit(limits)
            except Exception:
                pass

            time.sleep(self.interval)

    def stop(self) -> None:
        self._running = False
        self.wait()


# -------------------------------------------------------------
# Main Administrative Dashboard Window
# -------------------------------------------------------------

class SentinelDashboard(QtWidgets.QMainWindow):
    """Main administrative dashboard window with strict API connection gating."""

    def __init__(self, db: DatabaseManager):
        super().__init__()
        self.db = db
        self.auth_mgr = AuthManager(self.db)
        self.quota_mgr = QuotaManager(
            self.db,
            on_quota_breach=self._handle_quota_breach_from_daemon,
        )

        # Top-level floating alert overlay
        self.overlay = AlertOverlay(
            on_open_dashboard=self._bring_to_front,
            on_snooze=self._snooze_alerts,
        )

        # Initialize local network proxy
        self.proxy_daemon = NetworkProxyDaemon(
            quota_manager=self.quota_mgr,
            token_monitor=None,
        )
        self.proxy_daemon.start()

        # UI Setup
        self.setWindowTitle("Claude Sentinel — Unified Telemetry & Quota Enforcement")
        self.resize(1200, 800)
        self.setStyleSheet(DARK_STYLESHEET)

        self._init_ui()
        self._setup_system_tray()
        self._start_workers()

        # Apply initial connection gating state
        self._apply_connection_state(self.auth_mgr.is_authenticated())

    def _check_api_connected(self) -> bool:
        """Returns True only if confirmed that user connected and validated Anthropic API."""
        return self.auth_mgr.is_authenticated()

    def _guard_api_action(self, action_name: str) -> bool:
        """
        Enforces strict gating: if API is not connected, prevents the action,
        warns the user, and directs them to the Account & Security tab.
        """
        if not self._check_api_connected():
            QtWidgets.QMessageBox.warning(
                self,
                "API Not Connected",
                f"⚠️ Anthropic API Not Connected\n\n"
                f"The task '{action_name}' could not be performed until and unless "
                f"your software is connected and verified with your Anthropic Claude API.\n\n"
                f"Please link your Anthropic API Key in the 'Account & Security' tab first.",
            )
            # Switch to Account & Security tab (index 3)
            self.tabs.setCurrentIndex(3)
            return False
        return True

    def _init_ui(self) -> None:
        central_widget = QtWidgets.QWidget()
        central_widget.setObjectName("CentralWidget")
        self.setCentralWidget(central_widget)

        root_layout = QtWidgets.QVBoxLayout(central_widget)
        root_layout.setContentsMargins(24, 20, 24, 20)
        root_layout.setSpacing(18)

        # 1. Top Header Bar
        root_layout.addLayout(self._build_header_bar())

        # 2. Main Tab Widget
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._build_dashboard_tab(), "Live Telemetry")
        self.tabs.addTab(self._build_limits_tab(), "Quota & Hard Limits")
        self.tabs.addTab(self._build_activity_tab(), "Activity Timeline")
        self.tabs.addTab(self._build_auth_tab(), "Account & Security")

        # Intercept tab changes to protect locked features
        self.tabs.currentChanged.connect(self._handle_tab_changed)

        root_layout.addWidget(self.tabs)

        # 3. Status Bar
        self.status_bar = QtWidgets.QStatusBar()
        self.setStatusBar(self.status_bar)

    def _build_header_bar(self) -> QtWidgets.QHBoxLayout:
        """Construct high-impact header with status pulse badge and model tag."""
        header = QtWidgets.QHBoxLayout()

        # Logo / Title
        title_box = QtWidgets.QVBoxLayout()
        title_box.setSpacing(2)
        lbl_app = QtWidgets.QLabel("CLAUDE SENTINEL")
        lbl_app.setStyleSheet("font-size: 19px; font-weight: 900; color: #f5f6fa; letter-spacing: 1.5px;")
        lbl_sub = QtWidgets.QLabel("Dual-Environment Intelligence & Quota Throttling")
        lbl_sub.setStyleSheet("font-size: 12px; color: #718093;")
        title_box.addWidget(lbl_app)
        title_box.addWidget(lbl_sub)
        header.addLayout(title_box)

        header.addStretch()

        # Real-time Interface Status Pill
        self.pill_frame = QtWidgets.QFrame()
        self.pill_frame.setStyleSheet(
            """
            QFrame {
                background-color: #1e2230;
                border: 1px solid #2f354a;
                border-radius: 20px;
                padding: 4px 14px;
            }
            """
        )
        pill_layout = QtWidgets.QHBoxLayout(self.pill_frame)
        pill_layout.setContentsMargins(8, 4, 8, 4)
        pill_layout.setSpacing(8)

        self.pulse_dot = QtWidgets.QLabel("●")
        self.pulse_dot.setStyleSheet("color: #718093; font-size: 14px;")
        self.lbl_status = QtWidgets.QLabel("API Disconnected")
        self.lbl_status.setStyleSheet("color: #dcdde1; font-weight: bold; font-size: 12px;")

        pill_layout.addWidget(self.pulse_dot)
        pill_layout.addWidget(self.lbl_status)
        header.addWidget(self.pill_frame)

        # Active Model Badge
        self.badge_model = QtWidgets.QLabel("Model: API Disconnected")
        self.badge_model.setStyleSheet(
            """
            background-color: #161c29;
            color: #718093;
            border: 1px solid #273859;
            border-radius: 14px;
            padding: 6px 14px;
            font-size: 11px;
            font-weight: bold;
            """
        )
        header.addWidget(self.badge_model)

        return header

    def _build_dashboard_tab(self) -> QtWidgets.QWidget:
        """Construct the telemetry tab featuring a QStackedWidget (Locked vs Unlocked)."""
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        self.telemetry_stack = QtWidgets.QStackedWidget()

        # Page 0: Locked Screen (When API is not connected)
        self.locked_telemetry_view = self._build_locked_telemetry_view()
        self.telemetry_stack.addWidget(self.locked_telemetry_view)

        # Page 1: Unlocked Screen (Active telemetry cards, graphs, burn rates)
        self.unlocked_telemetry_view = self._build_unlocked_telemetry_view()
        self.telemetry_stack.addWidget(self.unlocked_telemetry_view)

        layout.addWidget(self.telemetry_stack)
        return widget

    def _build_locked_telemetry_view(self) -> QtWidgets.QWidget:
        """Create sleek locked placeholder screen shown until API is connected."""
        view = QtWidgets.QWidget()
        v_layout = QtWidgets.QVBoxLayout(view)
        v_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        card = QtWidgets.QFrame()
        card.setObjectName("LockedBanner")
        c_layout = QtWidgets.QVBoxLayout(card)
        c_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        c_layout.setSpacing(16)

        icon = QtWidgets.QLabel("🔒")
        icon.setStyleSheet("font-size: 48px;")
        icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        c_layout.addWidget(icon)

        title = QtWidgets.QLabel("ANTHROPIC API NOT CONNECTED")
        title.setStyleSheet("font-size: 20px; font-weight: 900; color: #ffa502; letter-spacing: 1px;")
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        c_layout.addWidget(title)

        msg = QtWidgets.QLabel(
            "Live model detection, token burn-rate graphs, and precision usage metrics are disabled.\n"
            "Until and unless you connect your Anthropic Claude account, telemetry cannot be captured."
        )
        msg.setStyleSheet("font-size: 13px; color: #a4b0be; line-height: 1.5;")
        msg.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        c_layout.addWidget(msg)

        btn_connect = QtWidgets.QPushButton("⚡ Connect Anthropic API to Unlock")
        btn_connect.setObjectName("GlowingCta")
        btn_connect.clicked.connect(lambda: self.tabs.setCurrentIndex(3))
        c_layout.addWidget(btn_connect, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)

        v_layout.addWidget(card)
        return view

    def _build_unlocked_telemetry_view(self) -> QtWidgets.QWidget:
        """Create the active telemetry view containing cards, burn-rate charts, and progress bar."""
        view = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # Metric Cards Row
        cards_layout = QtWidgets.QHBoxLayout()
        cards_layout.setSpacing(14)

        # Card 1: Total Tokens Today
        self.card_tokens_val = QtWidgets.QLabel("0")
        self.card_tokens_val.setProperty("class", "CardValue")
        self.card_tokens_sub = QtWidgets.QLabel("0 in / 0 out")
        self.card_tokens_sub.setProperty("class", "CardSub")
        cards_layout.addWidget(self._create_card("TOTAL TOKENS TODAY", self.card_tokens_val, self.card_tokens_sub))

        # Card 2: Cache Optimization
        self.card_cache_val = QtWidgets.QLabel("0")
        self.card_cache_val.setProperty("class", "CardValue")
        self.card_cache_sub = QtWidgets.QLabel("0 written / 0 read")
        self.card_cache_sub.setProperty("class", "CardSub")
        cards_layout.addWidget(self._create_card("CACHE HIT SAVINGS", self.card_cache_val, self.card_cache_sub))

        # Card 3: Estimated Daily Cost
        self.card_cost_val = QtWidgets.QLabel("$0.0000")
        self.card_cost_val.setProperty("class", "CardValue")
        self.card_cost_sub = QtWidgets.QLabel("Anthropic API Tier")
        self.card_cost_sub.setProperty("class", "CardSub")
        cards_layout.addWidget(self._create_card("ESTIMATED COST (USD)", self.card_cost_val, self.card_cost_sub))

        # Card 4: Total Calls
        self.card_calls_val = QtWidgets.QLabel("0")
        self.card_calls_val.setProperty("class", "CardValue")
        self.card_calls_sub = QtWidgets.QLabel("Code CLI + Web Chat")
        self.card_calls_sub.setProperty("class", "CardSub")
        cards_layout.addWidget(self._create_card("TOTAL INTERACTIONS", self.card_calls_val, self.card_calls_sub))

        layout.addLayout(cards_layout)

        # Quota Ceiling Progress Bar
        quota_card = QtWidgets.QFrame()
        quota_card.setProperty("class", "MetricCard")
        quota_layout = QtWidgets.QVBoxLayout(quota_card)
        quota_layout.setSpacing(8)

        q_header = QtWidgets.QHBoxLayout()
        self.lbl_quota_title = QtWidgets.QLabel("DAILY USAGE CEILING")
        self.lbl_quota_title.setProperty("class", "CardTitle")
        self.lbl_quota_text = QtWidgets.QLabel("0 / 3,000,000 Tokens (0.0%)")
        self.lbl_quota_text.setStyleSheet("color: #4cd137; font-weight: bold; font-size: 12px;")
        q_header.addWidget(self.lbl_quota_title)
        q_header.addStretch()
        q_header.addWidget(self.lbl_quota_text)
        quota_layout.addLayout(q_header)

        self.progress_quota = QtWidgets.QProgressBar()
        self.progress_quota.setRange(0, 100)
        self.progress_quota.setValue(0)
        quota_layout.addWidget(self.progress_quota)

        layout.addWidget(quota_card)

        # Real-time Token Burn-Rate Graph
        chart_card = QtWidgets.QFrame()
        chart_card.setProperty("class", "MetricCard")
        chart_layout = QtWidgets.QVBoxLayout(chart_card)
        chart_layout.setSpacing(10)

        lbl_chart = QtWidgets.QLabel("REAL-TIME TOKEN BURN RATE")
        lbl_chart.setProperty("class", "CardTitle")
        chart_layout.addWidget(lbl_chart)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground("#11131a")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self.plot_widget.setLabel("left", "Tokens per Call", color="#838896", size="10pt")
        self.plot_widget.setLabel("bottom", "Recent Interactivity Sequence", color="#838896", size="10pt")

        self.curve = self.plot_widget.plot(
            pen=pg.mkPen(color="#00d2d3", width=2.5),
            fillLevel=0,
            brush=pg.mkBrush(0, 210, 211, 45),
            symbol="o",
            symbolSize=6,
            symbolBrush="#00d2d3",
        )
        chart_layout.addWidget(self.plot_widget)

        layout.addWidget(chart_card)
        return view

    def _create_card(self, title: str, val_label: QtWidgets.QLabel, sub_label: QtWidgets.QLabel) -> QtWidgets.QFrame:
        """Helper to generate consistent metric cards."""
        card = QtWidgets.QFrame()
        card.setProperty("class", "MetricCard")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        lbl_title = QtWidgets.QLabel(title)
        lbl_title.setProperty("class", "CardTitle")
        layout.addWidget(lbl_title)
        layout.addWidget(val_label)
        layout.addWidget(sub_label)
        return card

    def _build_limits_tab(self) -> QtWidgets.QWidget:
        """Construct the Hard Limits & Quota management tab."""
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        lbl_desc = QtWidgets.QLabel(
            "Configure usage limits. When a ceiling is breached, the daemon enforces "
            "automatic process throttling (terminating Claude Code CLI) or triggers network routing blocks."
        )
        lbl_desc.setStyleSheet("color: #a4b0be; font-size: 12px; margin-bottom: 6px;")
        layout.addWidget(lbl_desc)

        # Limits Table
        self.table_limits = QtWidgets.QTableWidget()
        self.table_limits.setColumnCount(6)
        self.table_limits.setHorizontalHeaderLabels([
            "Limit Type",
            "Ceiling Value",
            "Current Tally",
            "Reset Interval (s)",
            "Enforcement Action",
            "Active",
        ])
        self.table_limits.horizontalHeader().setStretchLastSection(True)
        self.table_limits.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table_limits)

        # Edit Form Box
        form_frame = QtWidgets.QFrame()
        form_frame.setProperty("class", "MetricCard")
        form_layout = QtWidgets.QGridLayout(form_frame)
        form_layout.setSpacing(12)

        form_layout.addWidget(QtWidgets.QLabel("Select Limit:"), 0, 0)
        self.combo_limit_type = QtWidgets.QComboBox()
        self.combo_limit_type.addItems(["hourly_tokens", "daily_tokens", "daily_cost_usd", "hourly_calls"])
        form_layout.addWidget(self.combo_limit_type, 0, 1)

        form_layout.addWidget(QtWidgets.QLabel("Ceiling Value:"), 0, 2)
        self.spin_ceiling = QtWidgets.QDoubleSpinBox()
        self.spin_ceiling.setRange(1.0, 100_000_000.0)
        self.spin_ceiling.setValue(500000.0)
        form_layout.addWidget(self.spin_ceiling, 0, 3)

        form_layout.addWidget(QtWidgets.QLabel("Enforcement Action:"), 1, 0)
        self.combo_action = QtWidgets.QComboBox()
        self.combo_action.addItems(["warn_only", "block_and_alert", "kill_cli"])
        form_layout.addWidget(self.combo_action, 1, 1)

        form_layout.addWidget(QtWidgets.QLabel("Reset Interval (s):"), 1, 2)
        self.spin_interval = QtWidgets.QSpinBox()
        self.spin_interval.setRange(60, 604800)
        self.spin_interval.setValue(3600)
        form_layout.addWidget(self.spin_interval, 1, 3)

        btn_box = QtWidgets.QHBoxLayout()
        btn_save_limit = QtWidgets.QPushButton("Update Hard Limit")
        btn_save_limit.setObjectName("PrimaryButton")
        btn_save_limit.clicked.connect(self._handle_save_limit)
        btn_box.addWidget(btn_save_limit)

        btn_reset_tallies = QtWidgets.QPushButton("Reset All Tallies Now")
        btn_reset_tallies.clicked.connect(self._handle_reset_tallies)
        btn_box.addWidget(btn_reset_tallies)

        form_layout.addLayout(btn_box, 2, 0, 1, 4)
        layout.addWidget(form_frame)

        return widget

    def _build_activity_tab(self) -> QtWidgets.QWidget:
        """Construct the real-time activity event log table."""
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        lbl = QtWidgets.QLabel("Contextual Activity Engine: 2-Second Polling Stream")
        lbl.setProperty("class", "CardTitle")
        layout.addWidget(lbl)

        self.table_activity = QtWidgets.QTableWidget()
        self.table_activity.setColumnCount(4)
        self.table_activity.setHorizontalHeaderLabels([
            "Timestamp",
            "Process Name",
            "Window Title",
            "Classified Interface",
        ])
        self.table_activity.horizontalHeader().setStretchLastSection(True)
        self.table_activity.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table_activity.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table_activity.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table_activity)

        return widget

    def _build_auth_tab(self) -> QtWidgets.QWidget:
        """Construct the Anthropic Account & Key Management tab."""
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        auth_card = QtWidgets.QFrame()
        auth_card.setProperty("class", "MetricCard")
        c_layout = QtWidgets.QVBoxLayout(auth_card)
        c_layout.setSpacing(14)

        lbl_auth = QtWidgets.QLabel("ANTHROPIC CLAUDE ACCOUNT INTEGRATION")
        lbl_auth.setProperty("class", "CardTitle")
        c_layout.addWidget(lbl_auth)

        lbl_info = QtWidgets.QLabel(
            "Connect your Anthropic Claude account via API Key (sk-ant-...) to unlock live model tracking, "
            "token burn-rate graphs, and quota throttling. Credentials are encrypted at rest with hardware-bound AES-GCM.\n\n"
            "Tip: For rapid sandbox testing, you can use demo key 'sk-ant-demo' or 'sk-ant-test-01'."
        )
        lbl_info.setStyleSheet("color: #a4b0be; font-size: 12px; line-height: 1.4;")
        lbl_info.setWordWrap(True)
        c_layout.addWidget(lbl_info)

        # API Key input
        c_layout.addWidget(QtWidgets.QLabel("Anthropic API Key (sk-ant-api03-...):"))
        self.txt_api_key = QtWidgets.QLineEdit()
        self.txt_api_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.txt_api_key.setPlaceholderText("Enter your Anthropic API Key (e.g. sk-ant-api03-... or sk-ant-demo)")
        c_layout.addWidget(self.txt_api_key)

        # Web Session Key input
        c_layout.addWidget(QtWidgets.QLabel("Claude Web Session Cookie (sessionKey):"))
        self.txt_session_token = QtWidgets.QLineEdit()
        self.txt_session_token.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.txt_session_token.setPlaceholderText("Optional: Paste sessionKey from claude.ai browser cookies...")
        c_layout.addWidget(self.txt_session_token)

        # Current status label
        self.lbl_auth_status = QtWidgets.QLabel("Status: Not Connected (All Features Locked)")
        self.lbl_auth_status.setStyleSheet("font-weight: bold; color: #ffa502; font-size: 13px;")
        c_layout.addWidget(self.lbl_auth_status)

        # Action Buttons
        btn_row = QtWidgets.QHBoxLayout()
        btn_validate_save = QtWidgets.QPushButton("Connect & Unlock Software")
        btn_validate_save.setObjectName("PrimaryButton")
        btn_validate_save.clicked.connect(self._handle_save_credentials)
        btn_row.addWidget(btn_validate_save)

        btn_logout = QtWidgets.QPushButton("Disconnect / Logout")
        btn_logout.setObjectName("DangerButton")
        btn_logout.clicked.connect(self._handle_logout)
        btn_row.addWidget(btn_logout)

        c_layout.addLayout(btn_row)
        layout.addWidget(auth_card)
        layout.addStretch()

        self._refresh_auth_pane()
        return widget

    def _setup_system_tray(self) -> None:
        """Create system tray icon for seamless background running."""
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)
        icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon)
        self.tray_icon.setIcon(icon)

        tray_menu = QtWidgets.QMenu()
        act_show = tray_menu.addAction("Open Dashboard")
        act_show.triggered.connect(self._bring_to_front)
        tray_menu.addSeparator()
        act_quit = tray_menu.addAction("Quit Sentinel")
        act_quit.triggered.connect(QtWidgets.QApplication.instance().quit)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            self._bring_to_front()

    def _bring_to_front(self) -> None:
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _snooze_alerts(self) -> None:
        self.status_bar.showMessage("Alerts snoozed for 15 minutes.", 5000)

    # -------------------------------------------------------------
    # State Management & Gating
    # -------------------------------------------------------------

    def _apply_connection_state(self, is_connected: bool) -> None:
        """Apply locked vs unlocked state across header, telemetry view, and controls."""
        if is_connected:
            self.telemetry_stack.setCurrentIndex(1)  # Unlocked graphs view
            self.badge_model.setStyleSheet(
                "background-color: #122533; color: #00d2d3; border: 1px solid #00d2d3; border-radius: 14px; padding: 6px 14px; font-size: 11px; font-weight: bold;"
            )
            self.badge_model.setText("Model: Auto-Detecting")
            self.status_bar.showMessage("Anthropic API Connected. Real-time telemetry and quota daemon active.")
            self._refresh_telemetry()
            self._refresh_limits_table()
        else:
            self.telemetry_stack.setCurrentIndex(0)  # Locked placeholder view
            self.pulse_dot.setStyleSheet("color: #ffa502; font-size: 14px;")
            self.pill_frame.setStyleSheet(
                "background-color: #2b2010; border: 1px solid #ffa502; border-radius: 20px; padding: 4px 14px;"
            )
            self.lbl_status.setText("API Disconnected — Gated")
            self.badge_model.setStyleSheet(
                "background-color: #161c29; color: #718093; border: 1px solid #273859; border-radius: 14px; padding: 6px 14px; font-size: 11px; font-weight: bold;"
            )
            self.badge_model.setText("Model: API Disconnected")
            self.status_bar.showMessage("API Disconnected. Please connect an Anthropic account in the Account tab.")

    def _handle_tab_changed(self, index: int) -> None:
        """Prevent user from accessing Quota Limits or Activity Timeline until API is connected."""
        if not self._check_api_connected() and index in (1, 2):
            tab_name = self.tabs.tabText(index)
            # Revert back to Account tab and notify user
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(3)
            self.tabs.blockSignals(False)
            QtWidgets.QMessageBox.warning(
                self,
                "API Not Connected",
                f"⚠️ Anthropic API Not Connected\n\n"
                f"The task 'Access {tab_name}' could not be performed until and unless "
                f"your software is connected and verified with your Anthropic Claude API.\n\n"
                f"Please enter and test your Anthropic API Key below.",
            )

    # -------------------------------------------------------------
    # Background Workers Launch
    # -------------------------------------------------------------

    def _start_workers(self) -> None:
        """Instantiate and kick off asynchronous background QThreads."""
        self.activity_worker = QActivityWorker(self.db, poll_interval=2.0)
        self.activity_worker.tick_signal.connect(self._on_activity_tick)
        self.activity_worker.state_changed_signal.connect(self._on_activity_state_changed)
        self.activity_worker.start()

        self.token_worker = QTokenWorker(self.db, poll_interval=1.5)
        self.token_worker.metric_recorded_signal.connect(self._on_token_metric)
        self.token_worker.start()

        self.proxy_daemon.token_monitor = self.token_worker.monitor

        self.quota_worker = QQuotaWorker(self.quota_mgr, interval=2.5)
        self.quota_worker.quota_breach_signal.connect(self._on_quota_breach)
        self.quota_worker.limits_updated_signal.connect(self._on_limits_updated)
        self.quota_worker.start()

    # -------------------------------------------------------------
    # Slots & Real-time Event Handlers
    # -------------------------------------------------------------

    @QtCore.pyqtSlot(dict)
    def _on_activity_tick(self, data: dict) -> None:
        """Handle 2s activity tick from activity worker."""
        if not self._check_api_connected():
            return

        row_pos = 0
        self.table_activity.insertRow(row_pos)

        ts = datetime.fromtimestamp(data["timestamp"]).strftime("%H:%M:%S")
        self.table_activity.setItem(row_pos, 0, QtWidgets.QTableWidgetItem(ts))
        self.table_activity.setItem(row_pos, 1, QtWidgets.QTableWidgetItem(data.get("process_name", "")))
        self.table_activity.setItem(row_pos, 2, QtWidgets.QTableWidgetItem(data.get("window_title", "")))

        label_item = QtWidgets.QTableWidgetItem(data.get("readable_label", ""))
        if data["interface_type"] == "claude_chat":
            label_item.setForeground(QtGui.QColor("#00d2d3"))
        elif data["interface_type"] == "claude_code":
            label_item.setForeground(QtGui.QColor("#a55eea"))
        else:
            label_item.setForeground(QtGui.QColor("#718093"))

        self.table_activity.setItem(row_pos, 3, label_item)

        if self.table_activity.rowCount() > 100:
            self.table_activity.removeRow(100)

    @QtCore.pyqtSlot(dict)
    def _on_activity_state_changed(self, data: dict) -> None:
        """Update top status pill when user switches context (only if connected)."""
        if not self._check_api_connected():
            return

        label = data.get("readable_label", "Idle/Other Work")
        interface = data.get("interface_type", "idle_other")

        self.lbl_status.setText(label)

        if interface == "claude_chat":
            self.pulse_dot.setStyleSheet("color: #00d2d3; font-size: 14px;")
            self.pill_frame.setStyleSheet(
                "background-color: #0d282b; border: 1px solid #00d2d3; border-radius: 20px; padding: 4px 14px;"
            )
        elif interface == "claude_code":
            self.pulse_dot.setStyleSheet("color: #a55eea; font-size: 14px;")
            self.pill_frame.setStyleSheet(
                "background-color: #241433; border: 1px solid #a55eea; border-radius: 20px; padding: 4px 14px;"
            )
        else:
            self.pulse_dot.setStyleSheet("color: #718093; font-size: 14px;")
            self.pill_frame.setStyleSheet(
                "background-color: #1e2230; border: 1px solid #2f354a; border-radius: 20px; padding: 4px 14px;"
            )

    @QtCore.pyqtSlot(dict)
    def _on_token_metric(self, metric: dict) -> None:
        """Handle incoming token metric from Claude Code or Web Proxy."""
        if not self._check_api_connected():
            return

        model = metric.get("model_name", "Unknown")
        self.badge_model.setText(f"Model: {model}")
        self._refresh_telemetry()

    @QtCore.pyqtSlot(dict)
    def _on_quota_breach(self, breach: dict) -> None:
        """Display desktop warning overlay when limits are exceeded."""
        if not self._check_api_connected():
            return

        self.overlay.show_breach(breach)
        self.status_bar.showMessage(
            f"Quota Limit Exceeded: {breach['limit_type']} (Ceiling: {breach['ceiling_value']:,.0f})",
            8000,
        )

    def _handle_quota_breach_from_daemon(self, breach: dict) -> None:
        """Thread-safe callback from quota manager."""
        QtCore.QMetaObject.invokeMethod(
            self,
            "_on_quota_breach",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(dict, breach),
        )

    @QtCore.pyqtSlot(list)
    def _on_limits_updated(self, limits: list) -> None:
        """Update quota progress bar and limits table."""
        if self._check_api_connected():
            self._update_quota_progress(limits)

    def _update_quota_progress(self, limits: list) -> None:
        """Calculate primary ceiling progress bar."""
        daily_limit = next((l for l in limits if l["limit_type"] == "daily_tokens"), None)
        if not daily_limit:
            return

        ceiling = float(daily_limit["ceiling_value"])
        tally = float(daily_limit["current_tally"])
        pct = min(100.0, max(0.0, (tally / ceiling) * 100)) if ceiling > 0 else 0

        self.progress_quota.setValue(int(pct))
        self.lbl_quota_text.setText(f"{tally:,.0f} / {ceiling:,.0f} Tokens ({pct:.1f}%)")

        if pct < 75:
            self.lbl_quota_text.setStyleSheet("color: #4cd137; font-weight: bold; font-size: 12px;")
            self.progress_quota.setStyleSheet(
                "QProgressBar::chunk { background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10ac84, stop:1 #1dd1a1); }"
            )
        elif pct < 90:
            self.lbl_quota_text.setStyleSheet("color: #e1b12c; font-weight: bold; font-size: 12px;")
            self.progress_quota.setStyleSheet(
                "QProgressBar::chunk { background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #e1b12c, stop:1 #fbc531); }"
            )
        else:
            self.lbl_quota_text.setStyleSheet("color: #e84118; font-weight: bold; font-size: 12px;")
            self.progress_quota.setStyleSheet(
                "QProgressBar::chunk { background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #c23616, stop:1 #e84118); }"
            )

    def _refresh_telemetry(self) -> None:
        """Query SQLite database and update dashboard telemetry cards and graphs."""
        if not self._check_api_connected():
            return

        summary = self.db.get_token_summary_today()

        total_tok = summary.get("total_tokens", 0)
        in_tok = summary.get("input_tokens", 0)
        out_tok = summary.get("output_tokens", 0)
        cache_c = summary.get("cache_creation_tokens", 0)
        cache_r = summary.get("cache_read_tokens", 0)
        cost = summary.get("estimated_cost_usd", 0.0)
        calls = summary.get("call_count", 0)

        self.card_tokens_val.setText(f"{total_tok:,.0f}")
        self.card_tokens_sub.setText(f"{in_tok:,.0f} in / {out_tok:,.0f} out")

        self.card_cache_val.setText(f"{cache_r:,.0f}")
        self.card_cache_sub.setText(f"{cache_c:,.0f} written / {cache_r:,.0f} read")

        self.card_cost_val.setText(f"${cost:.4f}")
        self.card_calls_val.setText(f"{calls}")

        burn_data = self.db.get_burn_rate_series(limit=30)
        if burn_data:
            y_vals = [d["total_tokens"] for d in burn_data]
            x_vals = list(range(len(y_vals)))
            self.curve.setData(x_vals, y_vals)
            latest = burn_data[-1]
            if latest.get("model_name"):
                self.badge_model.setText(f"Model: {latest['model_name']}")

    def _refresh_limits_table(self) -> None:
        """Populate the limits configuration table."""
        limits = self.db.get_hard_limits()
        self.table_limits.setRowCount(len(limits))

        for row, l in enumerate(limits):
            self.table_limits.setItem(row, 0, QtWidgets.QTableWidgetItem(l["limit_type"]))
            self.table_limits.setItem(row, 1, QtWidgets.QTableWidgetItem(f"{l['ceiling_value']:,.2f}"))
            self.table_limits.setItem(row, 2, QtWidgets.QTableWidgetItem(f"{l['current_tally']:,.2f}"))
            self.table_limits.setItem(row, 3, QtWidgets.QTableWidgetItem(str(l["reset_interval_seconds"])))
            self.table_limits.setItem(row, 4, QtWidgets.QTableWidgetItem(l["action_type"]))
            self.table_limits.setItem(row, 5, QtWidgets.QTableWidgetItem("Yes" if l["is_active"] else "No"))

        self._update_quota_progress(limits)

    def _handle_save_limit(self) -> None:
        """Save modified limit with strict API connection check."""
        if not self._guard_api_action("Update Hard Limit"):
            return

        l_type = self.combo_limit_type.currentText()
        ceiling = self.spin_ceiling.value()
        action = self.combo_action.currentText()
        interval = self.spin_interval.value()

        self.db.update_hard_limit(
            limit_type=l_type,
            ceiling_value=ceiling,
            reset_interval_seconds=interval,
            action_type=action,
            is_active=True,
        )
        self._refresh_limits_table()
        self.status_bar.showMessage(f"Updated limit for {l_type}", 4000)

    def _handle_reset_tallies(self) -> None:
        """Reset all active limit counters with strict API connection check."""
        if not self._guard_api_action("Reset Tallies"):
            return

        with self.db.transaction() as conn:
            conn.execute("UPDATE hard_limits SET current_tally = 0.0, last_reset_timestamp = datetime('now')")
        self.quota_mgr.set_network_blocked(False)
        self.overlay.hide()
        self._refresh_limits_table()
        self.status_bar.showMessage("All quota tallies have been reset to zero.", 4000)

    def _refresh_auth_pane(self) -> None:
        """Check saved auth state and update UI."""
        is_auth = self.auth_mgr.is_authenticated()
        if is_auth:
            self.lbl_auth_status.setText("Status: ● Authenticated & Connected to Anthropic")
            self.lbl_auth_status.setStyleSheet("font-weight: bold; color: #10ac84; font-size: 13px;")
            key = self.auth_mgr.get_api_key()
            if key:
                masked = key[:7] + "..." + key[-4:] if len(key) > 12 else "********"
                self.txt_api_key.setText(masked)
        else:
            self.lbl_auth_status.setText("Status: 🔒 Not Connected (All Features Locked)")
            self.lbl_auth_status.setStyleSheet("font-weight: bold; color: #ffa502; font-size: 13px;")

    def _handle_save_credentials(self) -> None:
        """Validate and persist Anthropic API credentials, then unlock telemetry."""
        api_key = self.txt_api_key.text().strip()
        session_token = self.txt_session_token.text().strip()

        if not api_key and not session_token:
            QtWidgets.QMessageBox.warning(self, "Missing Credentials", "Please enter your Anthropic API Key.")
            return

        self.status_bar.showMessage("Validating credentials with Anthropic API...")
        QtWidgets.QApplication.processEvents()

        if "..." in api_key:
            api_key = self.auth_mgr.get_api_key() or ""

        success, msg = self.auth_mgr.save_credentials(
            api_key=api_key if api_key else None,
            session_token=session_token if session_token else None,
            validate=True if api_key else False,
        )

        if success:
            self._apply_connection_state(is_connected=True)
            self._refresh_auth_pane()
            QtWidgets.QMessageBox.information(
                self,
                "API Verified & Connected",
                "✅ Anthropic Claude Account Linked Successfully!\n\n"
                "All live telemetry, model detection, token burn-rate graphs, and quota limits "
                "are now active and unlocked.",
            )
            # Switch back to telemetry tab to display the unlocked live dashboard
            self.tabs.setCurrentIndex(0)
        else:
            self._apply_connection_state(is_connected=False)
            self._refresh_auth_pane()
            QtWidgets.QMessageBox.critical(
                self,
                "Validation Failed",
                f"❌ Connection to Anthropic Claude Failed:\n\n{msg}\n\n"
                "Features remain locked until a valid key is linked.",
            )

    def _handle_logout(self) -> None:
        """Clear credentials, logout, and lock software features."""
        self.auth_mgr.logout()
        self.txt_api_key.clear()
        self.txt_session_token.clear()
        self._apply_connection_state(is_connected=False)
        self._refresh_auth_pane()
        QtWidgets.QMessageBox.information(
            self,
            "Logged Out",
            "Anthropic credentials have been cleared.\nAll telemetry graphs and model tracking are now locked.",
        )
        self.tabs.setCurrentIndex(3)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Minimize to tray instead of quitting if tray is available."""
        if self.tray_icon.isVisible():
            self.hide()
            self.tray_icon.showMessage(
                "Claude Sentinel Running",
                "Claude Sentinel is active in the background.",
                QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
            event.ignore()
        else:
            self._cleanup_workers()
            event.accept()

    def _cleanup_workers(self) -> None:
        """Stop all background QThreads cleanly."""
        if hasattr(self, "activity_worker"):
            self.activity_worker.stop()
        if hasattr(self, "token_worker"):
            self.token_worker.stop()
        if hasattr(self, "quota_worker"):
            self.quota_worker.stop()
        if hasattr(self, "proxy_daemon"):
            self.proxy_daemon.stop()
