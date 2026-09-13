"""The stable three-surface Review composition for Shell V3."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Signal
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from tapesift.ui_v3.empty_review import ZeroPlayOverlayV3
from tapesift.ui_v3.export_surface import ExportPageV3
from tapesift.ui_v3.side_rail import SideRailV3
from tapesift.ui_v3.workspace_state import ReviewRailState


class ReviewActionMirrorV3(QFrame):
    """One action hierarchy, visible wherever Play Details is collapsed."""

    def __init__(self, *, sources: dict[str, QPushButton], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("V3ReviewActionMirror")
        grid = QGridLayout(self)
        # Locked collapsed-state action tray: a real docked chassis, not four
        # loose buttons hovering over the Tag Map.  The same 10/6 rhythm is
        # used by the expanded inspector action bar in the V3 theme.
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        specs = (
            ("export", "Export Clip", 0, 0, 2),
            ("package", "Package / Cut Up", 0, 1, 3),
            ("save", "Save Play", 1, 0, 2),
            ("next", "Save + Next", 1, 1, 3),
        )
        self.buttons: dict[str, QPushButton] = {}
        for key, label, row, column, stretch in specs:
            source = sources[key]
            button = QPushButton(label, self)
            button.setObjectName(f"V3ReviewAction{key.title()}")
            button.setProperty("primary", "true" if key == "next" else "false")
            button.setProperty("quiet", "true" if key == "export" else "false")
            button.setProperty("reviewAction", key)
            button.setMinimumHeight(38)
            button.setToolTip(source.toolTip())
            button.clicked.connect(source.click)
            grid.addWidget(button, row, column)
            grid.setColumnStretch(column, stretch)
            self.buttons[key] = button
        self.setFixedHeight(102)
        self.setFixedWidth(312)

    def sync(self, sources: dict[str, QPushButton]) -> None:
        for key, button in self.buttons.items():
            source = sources[key]
            button.setEnabled(source.isEnabled())
            button.setToolTip(source.toolTip())


class ReviewFooterV3(QFrame):
    """Full-width client chassis that visually ends Review above Windows."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("V3ReviewFooter")
        self.setFixedHeight(38)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.left_cell = QFrame(self)
        self.left_cell.setObjectName("V3ReviewFooterLeft")
        left = QHBoxLayout(self.left_cell)
        left.setContentsMargins(14, 0, 10, 0)
        left.setSpacing(8)
        indicator = QFrame(self.left_cell)
        indicator.setObjectName("V3ReviewFooterIndicator")
        indicator.setFixedSize(6, 6)
        self.local_label = QLabel("Local workspace")
        self.local_label.setToolTip("Local workspace")
        self.local_label.setObjectName("V3ReviewFooterLocal")
        left.addWidget(indicator)
        left.addWidget(self.local_label)
        left.addStretch(1)

        self.center_cell = QFrame(self)
        self.center_cell.setObjectName("V3ReviewFooterCenter")
        center = QHBoxLayout(self.center_cell)
        center.setContentsMargins(10, 0, 10, 0)
        self.shortcut_label = QLabel(
            "B / W  clips   ·   E  edit   ·   Esc  playback   ·   ?  shortcuts")
        self.shortcut_label.setObjectName("V3ReviewFooterShortcuts")
        self.shortcut_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.addWidget(self.shortcut_label, 1)

        self.right_cell = QFrame(self)
        self.right_cell.setObjectName("V3ReviewFooterRight")
        right = QHBoxLayout(self.right_cell)
        right.setContentsMargins(10, 0, 14, 0)
        self.count_label = QLabel("0 plays  ·  0 logged")
        self.count_label.setObjectName("V3ReviewFooterCount")
        right.addStretch(1)
        right.addWidget(self.count_label)

        row.addWidget(self.left_cell)
        row.addWidget(self.center_cell, 1)
        row.addWidget(self.right_cell)

    def set_tracks(self, left_width: int, right_width: int) -> None:
        self.left_cell.setFixedWidth(max(0, int(left_width)))
        self.right_cell.setFixedWidth(max(0, int(right_width)))
        self.local_label.setText("Local" if left_width < 200 else "Local workspace")
        self.set_status(*getattr(self, "_counts", (0, 0)))

    def set_status(self, play_count: int, logged_count: int) -> None:
        plays = max(0, int(play_count))
        logged = max(0, int(logged_count))
        play_word = "play" if plays == 1 else "plays"
        self._counts = (plays, logged)
        full = f"{plays} {play_word}  ·  {logged} logged"
        self.count_label.setToolTip(full)
        self.count_label.setText(
            full if self.right_cell.width() >= 180 else f"{plays} {play_word}")


class ReviewWorkspaceV3(QFrame):
    rail_state_changed = Signal(bool, bool)
    review_requested = Signal()

    LEDGER_OPEN_WIDTH = 326
    DETAILS_OPEN_WIDTH = 326
    COLLAPSED_WIDTH = 72

    def __init__(
            self, *, ledger: QWidget, workbench: QWidget,
            details: QWidget, export_surface: QWidget,
            action_sources: dict[str, QPushButton],
            film_surface: QWidget, detect_source: QPushButton,
            new_clip_source: QPushButton,
            initial_state: ReviewRailState, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("V3ReviewWorkspace")
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        review_row = QFrame(self)
        review_row.setObjectName("V3ReviewRow")
        row = QHBoxLayout(review_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.ledger_rail = SideRailV3(
            side="left", title="CLIP LEDGER", content=ledger,
            open_width=self.LEDGER_OPEN_WIDTH,
            collapsed_width=self.COLLAPSED_WIDTH,
            parent=self,
        )
        self.center_stack = QStackedWidget(self)
        self.center_stack.setObjectName("V3ReviewCenterStack")
        self.workbench = workbench
        self._action_sources = action_sources
        self.export_surface = export_surface
        self.export_page = ExportPageV3(export_surface, workbench)
        self.back_to_review_button = \
            self.export_page.back_to_review_button
        self.export_page.review_requested.connect(
            self.review_requested.emit)
        self.center_stack.addWidget(workbench)
        workbench_layout = workbench.layout()
        if not isinstance(workbench_layout, QVBoxLayout):
            raise RuntimeError("Review workbench is not a vertical layout")
        workbench_layout.addWidget(self.export_page)
        self.export_page.hide()
        self.details_rail = SideRailV3(
            side="right", title="PLAY DETAILS", content=details,
            open_width=self.DETAILS_OPEN_WIDTH,
            collapsed_width=self.COLLAPSED_WIDTH,
            footer="EXPORT",
            parent=self,
        )
        row.addWidget(self.ledger_rail)
        row.addWidget(self.center_stack, 1)
        row.addWidget(self.details_rail)
        shell.addWidget(review_row, 1)

        self.footer = ReviewFooterV3(self)
        shell.addWidget(self.footer)

        self.zero_overlay = ZeroPlayOverlayV3(
            film_surface=film_surface,
            detect_source=detect_source,
            new_clip_source=new_clip_source,
        )
        self._zero_mode = False

        self.action_mirror = ReviewActionMirrorV3(
            sources=action_sources, parent=workbench)
        workbench.installEventFilter(self)
        for source in action_sources.values():
            source.installEventFilter(self)

        self.ledger_rail.open_changed.connect(self._rail_changed)
        self.details_rail.open_changed.connect(self._rail_changed)
        self.set_rail_state(initial_state, emit=False)
        self.set_stage("review")

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.workbench and event.type() == QEvent.Type.Resize:
            self._position_action_mirror()
        elif watched in self._action_sources.values() and event.type() in {
                QEvent.Type.EnabledChange, QEvent.Type.ToolTipChange}:
            self.action_mirror.sync(self._action_sources)
        return super().eventFilter(watched, event)

    def _position_action_mirror(self) -> None:
        mirror = self.action_mirror
        # The tray is welded to the workbench's lower-right edges in both
        # ledger states.  The prior 22/28px margins made it read as a floating
        # popup and the 224px narrow variant clipped Package / Cut Up.
        mirror.setFixedWidth(312)
        mirror.move(
            max(0, self.workbench.width() - mirror.width()),
            max(0, self.workbench.height() - mirror.height()),
        )
        mirror.raise_()

    def _rail_changed(self, _opened: bool) -> None:
        state = self.rail_state()
        self._sync_action_mirror(state)
        self._sync_footer_tracks(state)
        self.rail_state_changed.emit(
            state.ledger_open, state.details_open)

    def _sync_action_mirror(self, state: ReviewRailState) -> None:
        # The locked collapsed Review has no floating action tray. Play
        # actions belong to the open Details rail and nowhere else.
        visible = False
        self.action_mirror.sync(self._action_sources)
        self.action_mirror.setVisible(visible)
        if visible:
            self._position_action_mirror()

    def rail_state(self) -> ReviewRailState:
        return ReviewRailState(
            ledger_open=self.ledger_rail.is_open(),
            details_open=self.details_rail.is_open(),
        )

    def set_rail_state(
            self, state: ReviewRailState, *, emit: bool = True) -> None:
        self.ledger_rail.set_open(state.ledger_open, emit=False)
        self.details_rail.set_open(state.details_open, emit=False)
        self._sync_action_mirror(state)
        self._sync_footer_tracks(state)
        if emit:
            self.rail_state_changed.emit(
                state.ledger_open, state.details_open)

    def set_stage(self, stage: str) -> None:
        self.center_stack.setCurrentWidget(self.workbench)
        self.export_page.setVisible(stage == "export")
        self._sync_action_mirror(self.rail_state())

    def set_ledger_summary(
            self, selected_play: str, play_count: int,
            logged_count: int = 0) -> None:
        self.ledger_rail.set_summary(selected_play, play_count)
        self.footer.set_status(play_count, logged_count)

    def _sync_footer_tracks(self, state: ReviewRailState) -> None:
        self.footer.set_tracks(
            self.ledger_rail.open_width if state.ledger_open
            else self.COLLAPSED_WIDTH,
            self.details_rail.open_width if state.details_open
            else self.COLLAPSED_WIDTH,
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # V3 rails already own fixed widths; narrow them without changing
        # the user's open/closed preference or replacing the content widgets.
        compact = self.width() < 1500
        for rail, width in ((self.ledger_rail, 300 if compact else self.LEDGER_OPEN_WIDTH),
                            (self.details_rail, 300 if compact else self.DETAILS_OPEN_WIDTH)):
            if rail.open_width != width:
                rail.open_width = width
                rail.set_open(rail.is_open(), emit=False)
        self._sync_footer_tracks(self.rail_state())

    def set_zero_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled and self.rail_state() != ReviewRailState(True, True):
            self.set_rail_state(ReviewRailState(True, True))
        self._zero_mode = enabled
        self.setProperty("zeroMode", enabled)
        self.zero_overlay.set_zero_mode(enabled)
        self.action_mirror.setEnabled(not enabled)
        self.style().unpolish(self)
        self.style().polish(self)
