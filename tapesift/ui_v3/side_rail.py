"""Stable open/collapsed side surfaces for Shell V3 Review."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QSizePolicy,
    QStackedLayout, QStyle, QToolButton, QVBoxLayout, QWidget,
)


class _ClickableRail(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _VerticalRailLabel(QGraphicsView):
    """Rotate real Qt text without rasterizing or replacing it with artwork."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("V3CollapsedRailTitleView")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.viewport().setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("background: transparent; border: none;")

        scene = QGraphicsScene(self)
        self.setScene(scene)
        label = QLabel(text)
        label.setObjectName("V3CollapsedRailTitle")
        font = QFont("IBM Plex Sans", 10)
        font.setWeight(QFont.Weight.DemiBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
        label.setFont(font)
        label.adjustSize()
        proxy = scene.addWidget(label)
        proxy.setTransformOriginPoint(proxy.boundingRect().center())
        proxy.setRotation(-90)
        scene.setSceneRect(proxy.mapToScene(
            proxy.boundingRect()).boundingRect())
        self.label = label
        self.proxy = proxy


class SideRailV3(QFrame):
    open_changed = Signal(bool)

    def __init__(
            self, *, side: str, title: str, content: QWidget,
            open_width: int, collapsed_width: int = 64,
            footer: str = "", parent=None) -> None:
        super().__init__(parent)
        if side not in {"left", "right"}:
            raise ValueError("side must be left or right")
        self.side = side
        self.content = content
        self.open_width = int(open_width)
        self.collapsed_width = int(collapsed_width)
        self._open = False
        self.setObjectName(
            "V3LedgerRail" if side == "left" else "V3DetailsRail")
        self.setProperty("side", side)
        self.setSizePolicy(QSizePolicy.Policy.Fixed,
                           QSizePolicy.Policy.Expanding)

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.setStackingMode(QStackedLayout.StackingMode.StackOne)

        self._collapsed_page = _ClickableRail(self)
        self._collapsed_page.setObjectName("V3CollapsedRail")
        collapsed = QVBoxLayout(self._collapsed_page)
        collapsed.setContentsMargins(6, 8, 6, 8)
        collapsed.setSpacing(8)
        self.expand_button = self._arrow_button(expand=True)
        self.expand_button.clicked.connect(lambda: self.set_open(True))
        collapsed.addWidget(
            self.expand_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.collapsed_title = _VerticalRailLabel(
            f"{title}  ·  OPEN", self._collapsed_page)
        collapsed.addWidget(self.collapsed_title, 1)
        self.selected_label = QLabel("--")
        self.selected_label.setObjectName("V3CollapsedRailSelection")
        self.selected_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.selected_label.setVisible(side == "left")
        collapsed.addWidget(self.selected_label)
        self.count_label = QLabel("0 PLAYS")
        self.count_label.setObjectName("V3CollapsedRailCount")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_label.setVisible(side == "left")
        collapsed.addWidget(self.count_label)
        collapsed.addStretch(1)
        if footer:
            footer_label = QLabel(footer)
            footer_label.setObjectName("V3CollapsedRailFooter")
            footer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            collapsed.addWidget(footer_label)
        self._collapsed_page.clicked.connect(lambda: self.set_open(True))

        self._open_page = QFrame(self)
        self._open_page.setObjectName("V3OpenRail")
        opened = QHBoxLayout(self._open_page)
        opened.setContentsMargins(0, 0, 0, 0)
        opened.setSpacing(0)
        # The panel header reserves this button's gutter. Only the header is
        # overlaid, so the table and scrollable details retain their full width.
        collapse_strip = QFrame(self._open_page)
        collapse_strip.setObjectName("V3RailCollapseStrip")
        collapse_strip.setFixedSize(28, 40)
        strip_layout = QVBoxLayout(collapse_strip)
        strip_layout.setContentsMargins(2, 6, 2, 6)
        strip_layout.setSpacing(0)
        self.collapse_button = self._arrow_button(expand=False)
        self.collapse_button.clicked.connect(lambda: self.set_open(False))
        strip_layout.addWidget(
            self.collapse_button,
            0,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
        )
        strip_layout.addStretch(1)
        content.setSizePolicy(QSizePolicy.Policy.Expanding,
                              QSizePolicy.Policy.Expanding)
        opened.addWidget(content, 1)
        self._collapse_strip = collapse_strip
        self._open_page.installEventFilter(self)

        self._stack.addWidget(self._collapsed_page)
        self._stack.addWidget(self._open_page)
        self.set_open(False, emit=False)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._open_page and event.type() in {
                QEvent.Type.Resize, QEvent.Type.Show}:
            self._position_collapse_strip()
        return super().eventFilter(watched, event)

    def _position_collapse_strip(self) -> None:
        self._collapse_strip.move(
            self._open_page.width() - 28 if self.side == "left" else 0, 0)
        self._collapse_strip.raise_()

    def _arrow_button(self, *, expand: bool) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName(
            "V3RailExpandButton" if expand else "V3RailCollapseButton")
        points_right = (self.side == "left") == expand
        pixmap = (
            QStyle.StandardPixmap.SP_ArrowRight
            if points_right else QStyle.StandardPixmap.SP_ArrowLeft
        )
        button.setIcon(self.style().standardIcon(pixmap))
        button.setArrowType(
            Qt.ArrowType.RightArrow if points_right else Qt.ArrowType.LeftArrow)
        button.setFixedSize(24, 24)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setAccessibleName(
            ("Open " if expand else "Close ")
            + ("Clip Ledger" if self.side == "left" else "Clip Details"))
        return button

    def is_open(self) -> bool:
        return self._open

    def set_open(self, opened: bool, *, emit: bool = True) -> None:
        opened = bool(opened)
        changed = opened != self._open
        self._open = opened
        self._stack.setCurrentWidget(
            self._open_page if opened else self._collapsed_page)
        # QDockWidget.hide() explicitly hides its child before V3 rehosts it.
        # A layout will resize a hidden child but will not reverse that explicit
        # visibility state, which left an apparently empty open rail.  The rail
        # now owns visibility after adoption.
        self.content.setVisible(opened)
        width = self.open_width if opened else self.collapsed_width
        self.setMinimumWidth(width)
        self.setMaximumWidth(width)
        self.setProperty("open", opened)
        self.style().unpolish(self)
        self.style().polish(self)
        if opened:
            self._position_collapse_strip()
        if changed and emit:
            self.open_changed.emit(opened)

    def set_summary(self, selected_play: str, play_count: int) -> None:
        self.selected_label.setText(selected_play or "--")
        self.count_label.setText(f"{max(0, int(play_count))} PLAYS")
