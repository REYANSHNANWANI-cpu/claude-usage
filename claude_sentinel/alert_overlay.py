"""Persistent top-level desktop alert banner overlay for quota breaches."""
from typing import Any, Callable, Dict, Optional
from PyQt6 import QtCore, QtGui, QtWidgets


class AlertOverlay(QtWidgets.QWidget):
    """
    Frameless, stay-on-top warning HUD banner displayed at top of screen
    when usage limits or budgets are crossed.
    """

    def __init__(
        self,
        on_open_dashboard: Optional[Callable[[], None]] = None,
        on_snooze: Optional[Callable[[], None]] = None,
        parent: Optional[QtWidgets.QWidget] = None,
    ):
        super().__init__(parent)
        self.on_open_dashboard = on_open_dashboard
        self.on_snooze = on_snooze

        # Frameless and Stay On Top
        self.setWindowFlags(
            QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)

        self._init_ui()

    def _init_ui(self) -> None:
        """Construct the HUD alert widget."""
        main_layout = QtWidgets.QHBoxLayout(self)
        main_layout.setContentsMargins(16, 12, 16, 12)

        # Container card
        card = QtWidgets.QFrame()
        card.setObjectName("AlertCard")
        card.setStyleSheet(
            """
            QFrame#AlertCard {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2b1115, stop:1 #1a0f12);
                border: 2px solid #ff4757;
                border-radius: 12px;
            }
            QLabel {
                color: #ffffff;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            }
            QPushButton {
                background-color: #38181e;
                color: #ff6b81;
                border: 1px solid #ff4757;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #ff4757;
                color: #ffffff;
            }
            QPushButton#PrimaryBtn {
                background-color: #ff4757;
                color: #ffffff;
            }
            QPushButton#PrimaryBtn:hover {
                background-color: #ff6b81;
            }
            """
        )

        card_layout = QtWidgets.QHBoxLayout(card)
        card_layout.setContentsMargins(20, 14, 20, 14)
        card_layout.setSpacing(18)

        # Pulse warning icon
        self.icon_label = QtWidgets.QLabel("⚠️")
        self.icon_label.setStyleSheet("font-size: 26px;")
        card_layout.addWidget(self.icon_label)

        # Message column
        text_layout = QtWidgets.QVBoxLayout()
        text_layout.setSpacing(3)

        self.title_label = QtWidgets.QLabel("USAGE QUOTA CEILING REACHED")
        self.title_label.setStyleSheet("font-size: 14px; font-weight: 800; color: #ff6b81; letter-spacing: 0.5px;")
        text_layout.addWidget(self.title_label)

        self.msg_label = QtWidgets.QLabel("Your configured Claude limit has been exceeded.")
        self.msg_label.setStyleSheet("font-size: 12px; color: #dcdde1;")
        text_layout.addWidget(self.msg_label)

        card_layout.addLayout(text_layout)
        card_layout.addStretch()

        # Action buttons
        btn_open = QtWidgets.QPushButton("Open Dashboard")
        btn_open.setObjectName("PrimaryBtn")
        btn_open.clicked.connect(self._handle_open_dashboard)
        card_layout.addWidget(btn_open)

        btn_snooze = QtWidgets.QPushButton("Snooze 15m")
        btn_snooze.clicked.connect(self._handle_snooze)
        card_layout.addWidget(btn_snooze)

        btn_close = QtWidgets.QPushButton("✕")
        btn_close.setFixedWidth(30)
        btn_close.clicked.connect(self.hide)
        card_layout.addWidget(btn_close)

        main_layout.addWidget(card)

        # Default size
        self.resize(760, 85)
        self._center_top()

    def _center_top(self) -> None:
        """Position the overlay banner horizontally centered near top of primary screen."""
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            x = geom.x() + (geom.width() - self.width()) // 2
            y = geom.y() + 24
            self.move(x, y)

    def show_breach(self, breach: Dict[str, Any]) -> None:
        """Populate breach metrics and make the HUD overlay visible."""
        limit_type = breach.get("limit_type", "Quota")
        ceiling = breach.get("ceiling_value", 0)
        tally = breach.get("current_tally", 0)
        action = breach.get("action_type", "warn_only")

        readable_name = limit_type.replace("_", " ").title()
        self.title_label.setText(f"QUOTA EXCEEDED: {readable_name.upper()}")

        if "cost" in limit_type:
            details = f"Tally: ${tally:,.2f} | Ceiling: ${ceiling:,.2f} | Action: {action.replace('_', ' ').title()}"
        else:
            details = f"Tally: {tally:,.0f} tokens | Ceiling: {ceiling:,.0f} | Action: {action.replace('_', ' ').title()}"

        self.msg_label.setText(details)
        self._center_top()
        self.show()
        self.raise_()

    def _handle_open_dashboard(self) -> None:
        self.hide()
        if self.on_open_dashboard:
            self.on_open_dashboard()

    def _handle_snooze(self) -> None:
        self.hide()
        if self.on_snooze:
            self.on_snooze()
