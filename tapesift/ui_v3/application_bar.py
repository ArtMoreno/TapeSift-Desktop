"""Native-frame application bar for Shell V3."""

from __future__ import annotations

from pathlib import Path

import shiboken6

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenuBar, QPushButton, QToolButton, QWidget

from tapesift.ui_core.layout_ownership import reparent_widget
from tapesift.ui_v2.components import CenteredApplicationMenu


class NativeApplicationBar(CenteredApplicationMenu):
    """Reuse TapeSift's real menu/actions without drawing window controls."""

    def __init__(self, menu_bar: QMenuBar, parent=None) -> None:
        super().__init__(menu_bar, parent)
        self._v3_suppressed_review_widgets: set[QWidget] = set()
        # The native menu actions keep their click handlers and shortcuts;
        # only suppress the legacy Alt-mnemonic marks absent from the lock.
        self._suppress_menu_mnemonics()
        self.setObjectName("TapeSiftV3ApplicationBar")
        self.setProperty("nativeShell", True)
        self.setFixedHeight(48)
        self.window_controls.hide()
        self.window_controls.setEnabled(False)
        # MainWindow adds its Window menu after this wrapper is constructed.
        self._queue_menu_mnemonic_suppression()
        export_host = QWidget(self)
        export_host.setObjectName("V3ExportMasthead")
        export_layout = QHBoxLayout(export_host)
        export_layout.setContentsMargins(18, 0, 0, 0)
        export_layout.setSpacing(12)
        brand = QLabel(export_host)
        wordmark = QPixmap(str(Path(__file__).resolve().parent.parent /
                               "resources/icons/tapesift-wordmark-white.png"))
        brand.setPixmap(wordmark.scaledToHeight(18, Qt.TransformationMode.SmoothTransformation))
        brand.setFixedSize(brand.pixmap().size())
        export_layout.addWidget(brand)
        mode_label = QLabel("EXPORT", export_host)
        mode_label.setObjectName("V3ExportModeLabel")
        export_layout.addWidget(mode_label)
        export_layout.addStretch(1)
        export_host.hide()
        self._left_hosts["export"] = export_host
        self._left_layouts["export"] = export_layout

    def _queue_menu_mnemonic_suppression(self) -> None:
        """Defer the menu pass without touching wrappers after window close."""
        QTimer.singleShot(0, self, self._suppress_menu_mnemonics)

    def _suppress_menu_mnemonics(self) -> None:
        """Keep real menus while matching the lock's underline-free labels."""
        for action in self.menu_bar.actions():
            menu = action.menu()
            clean = action.text().replace("&", "")
            if menu is not None:
                clean = menu.title().replace("&", "")
                if menu.title() != clean:
                    menu.setTitle(clean)
            if action.text() != clean:
                action.setText(clean)

    def set_mode(self, mode: str) -> None:
        self.setProperty("pageMode", mode)
        self.setFixedHeight(
            88 if mode == "home" else 72 if mode == "library" else 52 if mode == "export" else 40)
        super().set_mode(mode)
        self._suppress_menu_mnemonics()
        self.style().unpolish(self)
        self.style().polish(self)
        for widget in self.findChildren(QWidget):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        if mode in {"home", "library"}:
            for widget in (
                self.menu_bar,
                *self._left_hosts[mode].findChildren(QWidget),
                *self._action_hosts[mode].findChildren(QWidget),
            ):
                font = widget.font()
                font.setFamily("Segoe UI")
                widget.setFont(font)
                widget.setStyleSheet("")
        self._place_children()

    def add_review_left_widget(
            self, widget: QWidget, stretch: int = 0) -> None:
        object_name = widget.objectName()
        if object_name == "ReviewBrandLockup":
            # The locked workstation header carries TapeSift inside the client
            # as well as in the native title bar.  Reuse the existing brand
            # asset; do not redraw or substitute it.
            wordmark_path = (
                Path(__file__).resolve().parent.parent
                / "resources" / "icons" / "tapesift-wordmark-white.png")
            wordmark = QPixmap(str(wordmark_path))
            if not wordmark.isNull():
                widget.setPixmap(wordmark)
                widget.setProperty(
                    "brandAsset", "tapesift-wordmark-white.png")
            pixmap = widget.pixmap()
            if not pixmap.isNull():
                compact = pixmap.scaledToHeight(
                    14, Qt.TransformationMode.SmoothTransformation)
                widget.setPixmap(compact)
                widget.setFixedSize(compact.size())
            super().add_review_left_widget(widget, stretch)
            return
        if object_name == "ReviewBrandDivider":
            reparent_widget(widget, self)
            widget.hide()
            return
        if object_name in {
                "ReviewProjectButton", "ReviewProjectProgressLabel",
                "ReviewProjectProgress"}:
            # Project identity and progress belong to the locked Ledger
            # masthead in Review.  The original actions remain available from
            # the application menus and the widgets remain alive for state
            # synchronization while Pass 2 installs the final Ledger surface.
            reparent_widget(widget, self)
            widget.hide()
            self._v3_suppressed_review_widgets.add(widget)
            return
        super().add_review_left_widget(widget, stretch)

    def _sync_responsive_review_content(self) -> None:
        super()._sync_responsive_review_content()
        # The inherited compact-width rule may re-show the old project count
        # after it has been moved out of the locked Review masthead.
        for widget in getattr(
                self, "_v3_suppressed_review_widgets", set()):
            if shiboken6.isValid(widget):
                widget.hide()

    def _place_children(self) -> None:
        self._suppress_menu_mnemonics()
        self._sync_responsive_review_content()
        height = self.height()
        nav = getattr(self, "_film_navigation", None)
        if nav is not None:
            nav.setVisible(self._mode == "home")
        if self._mode == "home" and nav is not None:
            # Windows owns its caption buttons. Keep the real menus in a
            # small top strip and the layout's masthead immediately beneath.
            self.menu_bar.show()
            compact = self.width() < 1350
            self._left_layouts["home"].setSpacing(24 if compact else 40)
            gutter = 24 if compact else 38
            self.menu_bar.raise_()
            menu_width = self.menu_bar.sizeHint().width()
            self.menu_bar.setGeometry(max(0, self.width() - menu_width - 20), 0, menu_width, 24)
            brand_width = 335 if compact else 410
            self._left_hosts["home"].setGeometry(gutter, 24, brand_width, height - 24)
            actions_width = 314 if compact else 388
            for button in self._action_hosts["home"].findChildren(QPushButton):
                button.setFixedWidth(145 if compact else 182)
            self.right_host.setGeometry(self.width() - actions_width - gutter, 24, actions_width, height - 24)
            left = gutter + brand_width + 8
            right = self.right_host.x() - 8
            nav_width = min(402, max(300, right - left))
            nav.setGeometry(left + max(0, (right - left - nav_width) // 2), 24, nav_width, height - 24)
            nav.layout().setSpacing(4 if compact else 30)
            return
        menu_width = min(
            self.menu_bar.sizeHint().width(),
            max(240, self.width() - 440),
        )
        menu_left = max(0, (self.width() - menu_width) // 2)
        if self._mode == "review":
            menu_left = 100
        elif self._mode == "home":
            menu_left = max(660, self.width() - menu_width - 24)
        elif self._mode == "library":
            menu_left = max(self._left_layouts["library"].sizeHint().width() + 24,
                            self.width() - menu_width - 190)
            # QMenuBar provides its native overflow when the branding needs room.
            menu_width = min(menu_width, max(240, self.width() - menu_left - 190))
        menu_height = min(height, max(44, self.menu_bar.sizeHint().height()))
        self.menu_bar.setGeometry(menu_left, (height - menu_height) // 2, menu_width, menu_height)
        extension = self.menu_bar.findChild(QToolButton, "qt_menubar_ext_button")
        if extension is not None:
            from tapesift.ui_v3.icons import tinted_icon
            extension.setIcon(tinted_icon("more-horizontal-16.svg"))
            extension.setToolTip("More menus")
            extension.setAccessibleName("More menus")

        # Review begins after its collapsed rail. Home and Library instead
        # align to the normal page gutter.
        left_margin = (
            14 if self._mode == "review"
            else 60 if self._mode == "home"
            else 11
        )
        left_width = max(0, menu_left - left_margin - 12)
        for host in self._left_hosts.values():
            host.setGeometry(left_margin, 0, left_width, height)

        right_left = menu_left + menu_width + 8
        right_margin = 18 if self._mode == "library" else 8
        self.right_host.setGeometry(
            right_left,
            0,
            max(0, self.width() - right_left - right_margin),
            height,
        )

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.window() \
                and event.type() == QEvent.Type.WindowStateChange:
            self._place_children()
        return QWidget.eventFilter(self, watched, event)

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._mode == "home":
            from PySide6.QtGui import QColor, QPainter
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor("#11110f"))
            painter.setPen(QColor("#282722"))
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
            return
        super().paintEvent(event)
        if self._mode in {"home", "library"}:
            from tapesift.ui_v3.material import paint_slate
            paint_slate(self, header=True)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._suppress_menu_mnemonics()
        self._queue_menu_mnemonic_suppression()
        if self._mode == "home":
            self.set_mode("home")

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        event.ignore()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.ignore()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        event.ignore()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        event.ignore()
