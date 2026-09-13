"""Small V2-only presentation components."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMenuBar, QPushButton, QSizePolicy, QVBoxLayout,
    QStyle, QToolButton, QWidget,
)

from tapesift.ui_core.layout_ownership import detach_widget
from tapesift.ui_v2.icon_utils import tinted_standard_icon


class CenteredApplicationMenu(QWidget):
    """Frameless masthead with a permanently centered real QMenuBar.

    The menu bar and its existing QAction/QMenu objects are reparented into
    this presentation-only host. That preserves shortcuts, checked states,
    signals, keyboard navigation, and every existing command while allowing
    the menu group to sit at the geometric center of the application frame.
    Each primary page contributes its own left identity and right actions,
    while the actual application menu and window controls never move.
    """

    def __init__(self, menu_bar: QMenuBar, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CenteredApplicationMenu")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(56)
        self._drag_offset: QPoint | None = None

        self.menu_bar = menu_bar
        self.menu_bar.setObjectName("CenteredApplicationMenuBar")
        self.menu_bar.setNativeMenuBar(False)
        self.menu_bar.setParent(self)
        self.menu_bar.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )

        self._mode = ""
        self._left_hosts: dict[str, QWidget] = {}
        self._left_layouts: dict[str, QHBoxLayout] = {}
        self._action_hosts: dict[str, QWidget] = {}
        self._action_layouts: dict[str, QHBoxLayout] = {}

        for mode, object_name in (
            ("home", "HomeApplicationMasthead"),
            ("library", "LibraryApplicationMasthead"),
            ("review", "V2ReviewMasthead"),
        ):
            host = QWidget(self)
            host.setObjectName(object_name)
            host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            layout = QHBoxLayout(host)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(9)
            host.hide()
            self._left_hosts[mode] = host
            self._left_layouts[mode] = layout

        # Compatibility names retained for the Review workspace and its
        # focused tests.
        self.review_left_host = self._left_hosts["review"]
        self.review_left_layout = self._left_layouts["review"]

        self.right_host = QWidget(self)
        self.right_host.setObjectName("ApplicationTitleBarRight")
        self.right_layout = QHBoxLayout(self.right_host)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(8)
        self.right_layout.addStretch(1)

        for mode, object_name in (
            ("home", "HomeMastheadActions"),
            ("library", "LibraryMastheadActions"),
            ("review", "ReviewMastheadActions"),
        ):
            host = QWidget(self.right_host)
            host.setObjectName(object_name)
            layout = QHBoxLayout(host)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            host.hide()
            self.right_layout.addWidget(host)
            self._action_hosts[mode] = host
            self._action_layouts[mode] = layout

        self.review_actions_host = self._action_hosts["review"]
        self.review_actions_layout = self._action_layouts["review"]

        self.window_controls = QWidget(self.right_host)
        self.window_controls.setObjectName("ApplicationWindowControls")
        window_controls = QHBoxLayout(self.window_controls)
        window_controls.setContentsMargins(4, 0, 0, 0)
        window_controls.setSpacing(0)
        self.minimize_button = self._window_button(
            "ApplicationMinimizeButton",
            QStyle.StandardPixmap.SP_TitleBarMinButton,
            "Minimize TapeSift",
            lambda: self.window().showMinimized(),
        )
        self.maximize_button = self._window_button(
            "ApplicationMaximizeButton",
            QStyle.StandardPixmap.SP_TitleBarMaxButton,
            "Maximize TapeSift",
            self._toggle_maximized,
        )
        self.close_button = self._window_button(
            "ApplicationCloseButton",
            QStyle.StandardPixmap.SP_TitleBarCloseButton,
            "Close TapeSift",
            lambda: self.window().close(),
        )
        window_controls.addWidget(self.minimize_button)
        window_controls.addWidget(self.maximize_button)
        window_controls.addWidget(self.close_button)
        self.right_layout.addWidget(self.window_controls)

        self._compact_review_widgets: list[QWidget] = []
        if parent is not None:
            parent.installEventFilter(self)

    def add_mode_left_widget(
            self, mode: str, widget: QWidget, stretch: int = 0) -> None:
        detach_widget(widget)
        self._left_layouts[mode].addWidget(
            widget, stretch, Qt.AlignmentFlag.AlignVCenter)

    def add_mode_right_widget(self, mode: str, widget: QWidget) -> None:
        detach_widget(widget)
        self._action_layouts[mode].addWidget(
            widget, 0, Qt.AlignmentFlag.AlignVCenter)

    def add_mode_left_stretch(self, mode: str) -> None:
        self._left_layouts[mode].addStretch(1)

    def add_review_left_widget(
            self, widget: QWidget, stretch: int = 0) -> None:
        self.add_mode_left_widget("review", widget, stretch)

    def add_review_right_widget(self, widget: QWidget) -> None:
        self.add_mode_right_widget("review", widget)

    def hide_review_widgets_when_compact(
            self, *widgets: QWidget) -> None:
        self._compact_review_widgets.extend(widgets)
        self._sync_responsive_review_content()

    def set_review_mode(self, enabled: bool) -> None:
        self.set_mode("review" if enabled else "")

    def set_mode(self, mode: str) -> None:
        if mode and mode not in self._left_hosts:
            raise ValueError(f"Unknown application masthead mode: {mode}")
        self._mode = mode
        for name, host in self._left_hosts.items():
            host.setVisible(name == mode)
        for name, host in self._action_hosts.items():
            host.setVisible(name == mode)
        self._sync_responsive_review_content()
        self._place_children()

    def _sync_responsive_review_content(self) -> None:
        compact = self.width() < 1480
        for widget in self._compact_review_widgets:
            widget.setVisible(not compact)

    def _window_button(
            self, object_name: str,
            standard_pixmap: QStyle.StandardPixmap,
            accessible_name: str, slot) -> QToolButton:
        button = QToolButton(self.window_controls)
        button.setObjectName(object_name)
        button.setProperty("windowControl", "true")
        button.setIcon(tinted_standard_icon(
            self, standard_pixmap, size=QSize(14, 14)))
        button.setIconSize(QSize(14, 14))
        button.setFixedSize(38, 32)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setAccessibleName(accessible_name)
        button.setToolTip(accessible_name)
        button.clicked.connect(slot)
        return button

    def _toggle_maximized(self) -> None:
        window = self.window()
        if window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()
        self._sync_maximize_button()

    def _sync_maximize_button(self) -> None:
        maximized = self.window().isMaximized()
        icon = (
            QStyle.StandardPixmap.SP_TitleBarNormalButton
            if maximized
            else QStyle.StandardPixmap.SP_TitleBarMaxButton
        )
        self.maximize_button.setIcon(tinted_standard_icon(
            self, icon, size=QSize(14, 14)))
        self.maximize_button.setAccessibleName(
            "Restore TapeSift" if maximized else "Maximize TapeSift")
        self.maximize_button.setToolTip(
            self.maximize_button.accessibleName())

    def _place_children(self) -> None:
        self._sync_responsive_review_content()
        height = self.height()
        menu_width = min(
            self.menu_bar.sizeHint().width(),
            max(240, self.width() - 440),
        )
        menu_left = max(0, (self.width() - menu_width) // 2)
        self.menu_bar.setGeometry(menu_left, 0, menu_width, height)

        left_margin = 12
        left_width = max(0, menu_left - left_margin - 12)
        for host in self._left_hosts.values():
            host.setGeometry(left_margin, 0, left_width, height)

        right_left = menu_left + menu_width + 8
        self.right_host.setGeometry(
            right_left,
            0,
            max(0, self.width() - right_left - 8),
            height,
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_children()

    def eventFilter(self, watched, event) -> bool:
        if (
            watched is self.window()
            and event.type() == QEvent.Type.WindowStateChange
        ):
            self._sync_maximize_button()
        return super().eventFilter(watched, event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        window = self.window()
        handle = window.windowHandle()
        if handle is not None and handle.startSystemMove():
            self._drag_offset = None
        else:
            self._drag_offset = (
                event.globalPosition().toPoint()
                - window.frameGeometry().topLeft())
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and not self.window().isMaximized()
        ):
            self.window().move(
                event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class HomeHero(QWidget):
    """Website positioning adapted to a compact desktop-app welcome panel."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("hero", "true")
        row = QHBoxLayout(self)
        row.setContentsMargins(24, 18, 24, 18)
        row.setSpacing(24)

        copy = QVBoxLayout()
        copy.setSpacing(3)
        eyebrow = QLabel("WINDOWS DESKTOP  •  RUNS LOCALLY")
        eyebrow.setProperty("role", "eyebrow")
        title = QHBoxLayout()
        title.setSpacing(8)
        lead = QLabel("Cut film.")
        lead.setProperty("role", "display")
        accent = QLabel("Find plays.")
        accent.setProperty("role", "displayAccent")
        title.addWidget(lead)
        title.addWidget(accent)
        title.addStretch()
        body = QLabel(
            "Turn long football film into named, searchable clips - then "
            "review, log and export without leaving your desktop.")
        body.setProperty("role", "subtle")
        body.setWordWrap(True)
        copy.addWidget(eyebrow)
        copy.addLayout(title)
        copy.addWidget(body)
        row.addLayout(copy, 1)

        privacy = QVBoxLayout()
        privacy.setSpacing(4)
        local = QLabel("YOUR FILM STAYS HERE")
        local.setProperty("role", "eyebrow")
        proof = QLabel("No account\nNo uploads\nNo internet required")
        proof.setProperty("role", "subtle")
        privacy.addWidget(local)
        privacy.addWidget(proof)
        row.addLayout(privacy)


class WorkflowRibbon(QWidget):
    """A visible Detect → Review → Export map for the editing workspace."""

    detect_requested = Signal()
    review_requested = Signal()
    export_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("ribbon", "true")
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(12, 8, 12, 8)
        self._row.setSpacing(8)

        self.eyebrow = QLabel("WORKFLOW")
        self.eyebrow.setProperty("role", "eyebrow")
        self._row.addWidget(self.eyebrow)
        self._row.addSpacing(6)

        self.detect_btn = self._button("01  DETECT")
        self.review_btn = self._button("02  REVIEW")
        self.export_btn = self._button("03  EXPORT")
        self.detect_btn.clicked.connect(self.detect_requested.emit)
        self.review_btn.clicked.connect(self.review_requested.emit)
        self.export_btn.clicked.connect(self.export_requested.emit)
        self._row.addWidget(self.detect_btn)
        self._row.addWidget(self.review_btn)
        self._row.addWidget(self.export_btn)
        self._row.addStretch()

        self.hint = QLabel(
            "I / O mark  •  C cut at playhead  •  F5 review  •  "
            "Ctrl+E quick export")
        self.hint.setProperty("role", "subtle")
        self._row.addWidget(self.hint)
        self.compact = False

    def set_compact(self, enabled: bool) -> None:
        """Shrink to a segmented control that can share the project row.

        The label and the shortcut hint are the two pieces of this ribbon that
        never change, so they are what a compact layout gives up first: the
        hint moves to the status bar and the stage names carry themselves.
        """
        if self.compact == bool(enabled):
            return
        self.compact = bool(enabled)
        self.eyebrow.setVisible(not self.compact)
        self.hint.setVisible(not self.compact)
        self._row.setContentsMargins(
            *((0, 0, 0, 0) if self.compact else (12, 8, 12, 8)))
        self._row.setSpacing(0 if self.compact else 8)
        for index, button in enumerate(
                (self.detect_btn, self.review_btn, self.export_btn)):
            button.setText(
                ("DETECT", "REVIEW", "EXPORT")[index] if self.compact
                else ("01  DETECT", "02  REVIEW", "03  EXPORT")[index])
            button.setProperty(
                "workflowSeg", "true" if self.compact else "false")
            button.setProperty(
                "segPos", ("first", "middle", "last")[index])
            button.style().unpolish(button)
            button.style().polish(button)

    @staticmethod
    def _button(text: str) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("workflow", "true")
        return button

    def set_active(self, stage: str) -> None:
        for name, button in (
            ("detect", self.detect_btn),
            ("review", self.review_btn),
            ("export", self.export_btn),
        ):
            button.setProperty("active", "true" if name == stage else "false")
            button.style().unpolish(button)
            button.style().polish(button)
