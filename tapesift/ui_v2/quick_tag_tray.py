"""Compact one-click football labels for the V2 review workspace."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from tapesift.models.clip import Clip
from tapesift.services import result_service
from tapesift.ui_v2.elevation import apply_elevation, clear_elevation


@dataclass(frozen=True)
class QuickTag:
    key: str
    label: str
    details: tuple[tuple[str, str], ...]
    description: str


QUICK_TAGS: tuple[QuickTag, ...] = (
    QuickTag(
        "run", "Run", (("run_pass", "Run"),),
        "Set Run / Pass to Run"),
    QuickTag(
        "pass", "Pass", (("run_pass", "Pass"),),
        "Set Run / Pass to Pass"),
    QuickTag(
        "screen", "Screen",
        (("run_pass", "Pass"), ("play_type", "Screen")),
        "Set the play to Pass and Screen"),
    QuickTag(
        "rpo_run", "RPO Run",
        (("run_pass", "Run"), ("play_type", "RPO")),
        "Set the concept to RPO and the decision to Run"),
    QuickTag(
        "rpo_pass", "RPO Pass",
        (("run_pass", "Pass"), ("play_type", "RPO")),
        "Set the concept to RPO and the decision to Pass"),
    QuickTag(
        "play_action", "Play Action",
        (("play_action", "Play Action"),),
        "Toggle the independent Play Action modifier"),
    QuickTag(
        "scramble", "Scramble",
        (("run_pass", "Run"), ("play_type", "Scramble")),
        "Set the outcome to Run and the play type to Scramble"),
    QuickTag(
        "special", "Special", (("run_pass", "Special"),),
        "Set the play family to Special"),
    QuickTag(
        "no_play", "No Play", (("run_pass", "No Play"),),
        "Set the play family to No Play"),
    QuickTag(
        "sack", "Sack",
        (("run_pass", "Pass"), ("result", "Sack")),
        "Set the play to Pass and the result to Sack"),
    QuickTag(
        "interception", "INT",
        (("run_pass", "Pass"), ("result", "Interception")),
        "Set the play to Pass and the result to Interception"),
    QuickTag(
        "touchdown", "TD", (("result", "Touchdown"),),
        "Set Result of Play to Touchdown"),
    # Additional common results and highlights: quick tags earn their place
    # by being one-click labels people actually reach for on every game, so
    # anything below has to be as common as the seven above.
    QuickTag(
        "first_down", "First Down", (("result", "First Down"),),
        "Add First Down to Result of Play"),
    QuickTag(
        "fumble", "Fumble", (("result", "Fumble"),),
        "Set Result of Play to Fumble"),
    QuickTag(
        "field_goal", "Field Goal", (("result", "Field Goal Good"),),
        "Set Result of Play to Field Goal Good"),
    QuickTag(
        "penalty", "Penalty", (("result", "Penalty Accepted"),),
        "Add Penalty Accepted to Result of Play"),
)

_QUICK_TAGS_BY_KEY = {tag.key: tag for tag in QUICK_TAGS}


_TAG_VISUAL_FAMILIES = {
    "run": "run",
    "pass": "pass",
    "screen": "screen",
    "rpo_run": "rpo",
    "rpo_pass": "rpo",
    "sack": "sack",
    "interception": "interception",
    "touchdown": "touchdown",
    "first_down": "firstDown",
    "fumble": "fumble",
    "penalty": "penalty",
}


def quick_tag_visual_family(tag: "QuickTag") -> str:
    """Return a stable stylesheet family without changing tag semantics."""
    if tag.key in _TAG_VISUAL_FAMILIES:
        return _TAG_VISUAL_FAMILIES[tag.key]
    details = {field: value.casefold() for field, value in tag.details}
    result = " ".join((details.get("result", ""),
                       details.get("action", "")))
    for needle, family in (
            ("penalty", "penalty"), ("interception", "interception"),
            ("touchdown", "touchdown"), ("first down", "firstDown"),
            ("fumble", "fumble"), ("sack", "sack")):
        if needle in result:
            return family
    play_type = details.get("play_type", "")
    if play_type == "screen":
        return "screen"
    if play_type == "rpo":
        return "rpo"
    run_pass = details.get("run_pass", "")
    if run_pass in {"run", "pass"}:
        return run_pass
    return "result" if result.strip() else "neutral"

# The everyday rail is intentionally limited to the actions visible in the
# standard Review layout. The complete vocabulary remains available through
# Manage, so this gains button real estate without removing any capability.
DEFAULT_QUICK_TAG_KEYS = (
    "run",
    "pass",
    "screen",
    "rpo_run",
    "rpo_pass",
    "sack",
    "interception",
    "touchdown",
    "first_down",
)
DEFAULT_QUICK_TAGS: tuple[QuickTag, ...] = tuple(
    _QUICK_TAGS_BY_KEY[key] for key in DEFAULT_QUICK_TAG_KEYS
)

RUN_PASS_LAB_TAG_KEYS = (
    "run",
    "pass",
    "rpo_run",
    "rpo_pass",
    "play_action",
    "screen",
    "scramble",
    "sack",
    "special",
    "no_play",
)
RUN_PASS_LAB_TAGS: tuple[QuickTag, ...] = tuple(
    _QUICK_TAGS_BY_KEY[key] for key in RUN_PASS_LAB_TAG_KEYS
)


#: Fields that record what the play *produced*. Everything else describes
#: what the play *was*. Outcome wins when a tag sets both - "INT" also sets
#: run_pass to Pass, but an analyst reaches for it as a result.
#:
#: Derived rather than a fixed index, so the divider still lands correctly
#: when favourites are reordered or replaced.
_RESULT_FIELDS = frozenset({"result"})


def tag_group(tag: "QuickTag") -> str:
    """"result" when a tag records an outcome, otherwise "play"."""
    for field, _value in tag.details:
        if field in _RESULT_FIELDS:
            return "result"
    return "play"


def quick_tag_details(key: str) -> dict[str, str]:
    """Return a fresh detail mapping for a quick-tag action."""
    tag = _QUICK_TAGS_BY_KEY.get(key)
    return dict(tag.details) if tag is not None else {}


def load_quick_tags(raw: object) -> tuple[QuickTag, ...]:
    """Load saved favorites defensively; invalid settings fall back safely."""
    if not isinstance(raw, list) or not raw:
        return DEFAULT_QUICK_TAGS
    tags: list[QuickTag] = []
    seen: set[str] = set()
    explicit_keys = {
        str(value.get("key", "")).strip()
        for value in raw
        if isinstance(value, dict)
    }
    allowed_fields = {
        "quarter", "run_pass", "action", "play_type", "play_action",
        "result",
    }
    for value in raw:
        if not isinstance(value, dict):
            continue
        key = str(value.get("key", "")).strip()
        label = str(value.get("label", "")).strip()[:22]
        raw_details = value.get("details", {})
        if not key or key in seen or not label \
                or not isinstance(raw_details, dict):
            continue
        details = tuple(
            (str(field), str(detail).strip())
            for field, detail in raw_details.items()
            if str(field) in allowed_fields and str(detail).strip()
        )
        if not details:
            continue
        # The old built-in RPO action recorded only the concept, leaving the
        # run/pass decision ambiguous. Expand that exact legacy definition in
        # place so customized trays receive both explicit actions.
        if key == "rpo" and {
                field: detail.casefold() for field, detail in details
        } == {"play_type": "rpo"}:
            for migrated in QUICK_TAGS:
                if migrated.key not in {"rpo_run", "rpo_pass"} \
                        or migrated.key in seen \
                        or migrated.key in explicit_keys:
                    continue
                tags.append(migrated)
                seen.add(migrated.key)
            continue
        seen.add(key)
        tags.append(QuickTag(
            key, label, details,
            str(value.get("description", "")).strip()
            or f"Save {label} details",
        ))
    return tuple(tags) if tags else DEFAULT_QUICK_TAGS


def serialize_quick_tags(
        tags: tuple[QuickTag, ...]) -> list[dict[str, object]]:
    """Return a JSON-safe representation for AppSettings."""
    return [
        {
            "key": tag.key,
            "label": tag.label,
            "details": dict(tag.details),
            "description": tag.description,
        }
        for tag in tags
    ]


class QuickTagTray(QFrame):
    """One-click favorites rail that reflects and edits the selected clip."""

    tag_requested = Signal(str)
    manage_requested = Signal(bool)

    #: How far a visible tag may be stretched to fill the inline rail.
    MAX_INLINE_STRETCH = 40
    #: Bar B chip height. Applied on build and re-applied when the
    #: rail goes inline, because the inline path re-lays the row.
    #: Outer chip height. The stylesheet value is content-box, so
    #: 1px padding and a 1px border on each edge are taken off.
    CHIP_HEIGHT = 24

    def __init__(
            self, favorites: object = None,
            parent: QWidget | None = None) -> None:
        # Backward-compatible constructor: QuickTagTray(parent).
        if isinstance(favorites, QWidget) and parent is None:
            parent = favorites
            favorites = None
        super().__init__(parent)
        self.setObjectName("V2QuickTagTray")
        self.setProperty("quickTagTray", "true")
        apply_elevation(self)
        self.tags = load_quick_tags(favorites)
        self._clip: Clip | None = None
        self._inline = False
        self._natural_widths: dict[str, int] = {}

        row = QHBoxLayout(self)
        row.setContentsMargins(9, 5, 9, 5)
        row.setSpacing(6)

        self.heading = QLabel("QUICK TAG")
        self.heading.setProperty("role", "eyebrow")
        self.heading.setToolTip(
            "One click saves the label to the selected clip. Ctrl+Z undoes it.")
        row.addWidget(self.heading)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("V2QuickTagScroll")
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.scroll.setMinimumWidth(80)
        self.scroll.viewport().installEventFilter(self)

        self.button_host = QWidget()
        self.button_host.setObjectName("V2QuickTagButtonHost")
        self.button_row = QHBoxLayout(self.button_host)
        self.button_row.setContentsMargins(0, 0, 0, 0)
        self.button_row.setSpacing(6)
        self.scroll.setWidget(self.button_host)
        # Left-aligned: inline the rail is sized to whole buttons, so any
        # leftover must collect in one place rather than be split into two
        # gaps either side of the tags.
        row.addWidget(self.scroll, 1, Qt.AlignmentFlag.AlignLeft)

        self.buttons: dict[str, QPushButton] = {}
        self._rebuild_buttons()

        # Inline the rail cannot scroll: a scrolled rail clips a button
        # mid-label, and "RPO Pas" is exactly the ambiguity the labels are
        # sized to avoid. Whatever does not fit whole goes in here instead.
        self.more_button = QToolButton()
        self.more_button.setObjectName("V2QuickTagMoreButton")
        self.more_button.setProperty("quickTagControl", "true")
        self.more_button.setFixedWidth(66)
        self.more_button.clicked.connect(self._show_more_popup)
        self.more_button.hide()
        row.addWidget(self.more_button)

        # The overflow panel is a native top-level window, and only the
        # inline rail ever overflows, so it is built on demand rather than
        # once per tray.
        self.more_popup: QFrame | None = None
        self.more_grid: QGridLayout | None = None
        self.more_add_button: QToolButton | None = None
        self.more_buttons: dict[str, QPushButton] = {}

        self.add_button = QToolButton()
        self.add_button.setObjectName("V2QuickTagAddButton")
        self.add_button.setProperty("quickTagControl", "true")
        self.add_button.setText("Add")
        self.add_button.setAccessibleName("Add quick tag")
        self.add_button.setToolTip("Add a custom quick tag")
        self.add_button.setFixedWidth(44)
        self.add_button.clicked.connect(
            lambda: self.manage_requested.emit(True))
        row.addWidget(self.add_button)

        self.manage_button = QPushButton("Manage")
        self.manage_button.setObjectName("V2QuickTagManageButton")
        self.manage_button.setProperty("quickTagControl", "true")
        self.manage_button.setToolTip(
            "Add, edit, remove, or reorder quick tags")
        self.manage_button.setFixedWidth(76)
        self.manage_button.clicked.connect(
            lambda: self.manage_requested.emit(False))
        row.addWidget(self.manage_button)

    def _rebuild_buttons(self) -> None:
        while self.button_row.count():
            item = self.button_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.buttons.clear()
        # Cached widths belong to the tag set being replaced; a relabelled
        # key would otherwise keep measuring against its old label.
        self._natural_widths.clear()
        self.dividers: list[QFrame] = []
        previous_group: str | None = None
        for tag in self.tags:
            # Parent before polishing: the quick-tag font is selected by a
            # tray-ancestor stylesheet, so an unparented button measures
            # itself with the smaller application default and later clips.
            button = QPushButton(tag.label, self.button_host)
            button.setProperty("quickTag", "true")
            button.setProperty("tagFamily", quick_tag_visual_family(tag))
            # The stylesheet raises quick-tag text to 14px. Polish before
            # measuring so the minimum width is based on the font users
            # actually see, including in a newly floated player window.
            button.ensurePolished()
            # The rail scrolls when space is tight; labels must never be
            # squeezed into ambiguous text such as "RPO Pas".
            label_width = button.fontMetrics().horizontalAdvance(tag.label)
            # Width stays at label + 30: that margin is what keeps
            # 'RPO Pass' from eliding to 'RPO Pas', and the suite
            # asserts it. Bar B takes its compactness out of the
            # chip's height and padding, never out of this margin.
            button.setMinimumWidth(max(52, label_width + 30))
            # Set on the button itself, not via the tray or the app
            # sheet: VideoPlayer carries its own stylesheet, and a
            # widget's sheet outranks the application's for every
            # child beneath it - which is what kept resetting these
            # chips to 33px however often the code asked for 24.
            button.setStyleSheet(
                f"padding: 1px 8px;"
                f" min-height: {self.CHIP_HEIGHT - 4}px;"
                f" max-height: {self.CHIP_HEIGHT - 4}px;")
            button.setFixedHeight(self.CHIP_HEIGHT)
            # Remembered before any inline stretching, so successive resizes
            # measure against the label and not against the last stretch.
            self._natural_widths[tag.key] = button.minimumWidth()
            button.setCheckable(True)
            button.setEnabled(False)
            button.setAccessibleName(f"Quick tag {tag.label}")
            button.setToolTip(f"{tag.description}. Saves immediately.")
            button.clicked.connect(
                lambda _checked=False, key=tag.key:
                self.tag_requested.emit(key))
            group = tag_group(tag)
            if previous_group is not None and group != previous_group:
                divider = QFrame(self.button_host)
                divider.setProperty("quickTagDivider", "true")
                divider.setFrameShape(QFrame.Shape.NoFrame)
                divider.setFixedWidth(1)
                divider.setFixedHeight(14)
                self.button_row.addSpacing(3)
                self.button_row.addWidget(
                    divider, 0, Qt.AlignmentFlag.AlignVCenter)
                self.button_row.addSpacing(3)
                self.dividers.append(divider)
            previous_group = group
            self.button_row.addWidget(button)
            self.buttons[tag.key] = button
        self.button_row.addStretch(1)
        self.button_host.adjustSize()
        # Measured from the row itself rather than re-derived from the
        # buttons: the layout already knows about the group dividers and
        # their spacers, and every attempt to predict that total left the
        # host a couple of dozen pixels short, clipping the last chip.
        self.button_row.activate()
        self._ideal_scroll_width = self.button_row.sizeHint().width() + 4
        self.button_host.setFixedSize(
            self._ideal_scroll_width, self.CHIP_HEIGHT)
        self._fit_scroll()

    def set_tags(self, tags: tuple[QuickTag, ...]) -> None:
        self.tags = tags or DEFAULT_QUICK_TAGS
        self._rebuild_buttons()
        self.set_clip(self._clip)

    def details(self, key: str) -> dict[str, str]:
        tag = next((item for item in self.tags if item.key == key), None)
        if tag is None:
            # Hidden from the tighter default rail does not mean deleted from
            # the vocabulary: Manage, saved custom layouts, and command
            # routing can still resolve every built-in tag.
            tag = _QUICK_TAGS_BY_KEY.get(key)
        return dict(tag.details) if tag is not None else {}

    def set_clip(self, clip: Clip | None) -> None:
        """Enable actions and show which labels match the selected clip."""
        self._clip = clip
        details = clip.details if clip is not None else {}
        for tag in self.tags:
            button = self.buttons[tag.key]
            button.setEnabled(clip is not None)
            active = bool(clip) and all(
                result_service.has_result(
                    details.get(key, ""), value)
                if key == "result"
                else details.get(key, "").strip().casefold()
                == value.casefold()
                for key, value in tag.details
            )
            button.setChecked(active)
        if self._inline:
            # The overflow entries mirror button state, so they have to be
            # restocked whenever that state changes.
            self._fit_scroll()

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        if watched is self.scroll.viewport() \
                and event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta().y()
            bar = self.scroll.horizontalScrollBar()
            bar.setValue(bar.value() - delta)
            return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        self._fit_scroll()

    def set_inline(self, inline: bool) -> None:
        """Present as a strip inside the transport row rather than a card.

        The rail keeps its own scrolling and its own tag state; only the
        chrome that a row cannot afford - the card, the eyebrow, and the
        Add shortcut that Manage already covers - is dropped.
        """
        self._inline = inline
        self.setProperty("inline", "true" if inline else "false")
        if inline:
            clear_elevation(self)
        else:
            apply_elevation(self)
        self.setFrameShape(
            QFrame.Shape.NoFrame if inline else QFrame.Shape.StyledPanel)
        layout = self.layout()
        layout.setContentsMargins(0, 0, 0, 0) if inline \
            else layout.setContentsMargins(9, 5, 9, 5)
        self.heading.setVisible(not inline)
        for button in self.buttons.values():
            button.setFixedHeight(self.CHIP_HEIGHT)
        for control in (self.more_button, self.manage_button,
                        self.add_button):
            control.setFixedHeight(self.CHIP_HEIGHT)
        if inline:
            self.add_button.hide()
        else:
            self.more_button.hide()
            for key, button in self.buttons.items():
                button.setMinimumWidth(self._natural_widths.get(key, 52))
                button.setMaximumWidth(16777215)
                button.show()
        self.style().unpolish(self)
        self.style().polish(self)
        self._fit_scroll()

    def _fit_scroll(self) -> None:
        if not hasattr(self, "manage_button"):
            return
        layout = self.layout()
        margins = layout.contentsMargins()
        visible_controls = [
            control for control in
            (self.more_button, self.add_button, self.manage_button)
            if not control.isHidden()
        ]
        # A hidden heading must not keep reserving its width, or the rail
        # loses ~90px of room for no visible reason.
        heading_width = (
            self.heading.sizeHint().width()
            if not self.heading.isHidden() else 0)
        fixed_width = (
            heading_width
            + sum(control.sizeHint().width()
                  for control in visible_controls)
            + margins.left() + margins.right()
            + layout.spacing() * (2 + len(visible_controls))
        )
        available = max(80, self.width() - fixed_width)
        width = self._whole_button_width(available) if self._inline \
            else available
        if self.scroll.width() != width:
            self.scroll.setFixedWidth(width)

    def _whole_button_width(self, available: int) -> int:
        """Widest prefix of whole buttons that fits, and stock More with the rest.

        Returns the width to give the rail. Because it always lands on a
        button boundary the rail never shows a sliver of the next label.
        """
        spacing = self.button_row.spacing()
        keys = [tag.key for tag in self.tags if tag.key in self.buttons]
        ordered = [self.buttons[key] for key in keys]
        naturals = [self._natural_widths.get(key, 52) for key in keys]

        # A group divider is a real widget in the row: 1px rule, 3px of
        # air either side, and its own layout gap. Leaving it out of the
        # budget made the rail wider than this function believed, and
        # the last chip - First Down - was clipped by the viewport edge.
        groups = [tag_group(tag) for tag in self.tags
                  if tag.key in self.buttons]
        # addSpacing(3) + the 1px rule + addSpacing(3) are three extra
        # items in the row, so the layout also inserts three more of its
        # own gaps. Counting only one gap left the row ~12px wider than
        # this budget believed, which clipped the last chip.
        divider_cost = 7 + spacing * 3

        def fit(budget: int) -> tuple[int, int]:
            shown = used = 0
            previous_group = None
            for index, width in enumerate(naturals):
                step = width + (spacing if shown else 0)
                group = groups[index] if index < len(groups) else None
                if previous_group is not None and group != previous_group:
                    step += divider_cost
                if used + step > budget:
                    break
                used += step
                shown += 1
                previous_group = group
            return shown, used

        # Try without reserving anything first: if every tag fits, More is
        # not needed and must not be paid for.
        shown, used = fit(available)
        overflowing = shown < len(naturals)
        if overflowing:
            reserve = self.more_button.sizeHint().width() + spacing
            shown, used = fit(available - reserve)

        # Spend the remainder on the tags that made the cut so the rail ends
        # flush, but cap it - one lonely tag stretched across the flank reads
        # as a mistake rather than as a button.
        budget = available - (
            self.more_button.sizeHint().width() + spacing if overflowing else 0)
        slack = max(0, budget - used)
        bonus, extra = divmod(slack, shown) if shown else (0, 0)
        bonus = min(bonus, self.MAX_INLINE_STRETCH)
        for index, button in enumerate(ordered[:shown]):
            width = naturals[index] + bonus
            if bonus < self.MAX_INLINE_STRETCH and index < extra:
                width += 1
            button.setFixedWidth(width)
            used += width - naturals[index]

        for index, button in enumerate(ordered):
            button.setVisible(index < shown)
        hidden = ordered[shown:]
        self._stock_more_popup(hidden)
        self.more_button.setVisible(bool(hidden))
        self.more_button.setText(f"More +{len(hidden)}")

        # Re-measure after stretching: the bonus above widens the chips,
        # so a host sized before it is now too small and clips the last
        # one. Ask the row how wide it actually ended up.
        self.button_row.activate()
        content = self.button_row.sizeHint().width() + 4
        self.button_host.setFixedWidth(content)
        return max(0, min(content, available))

    POPUP_COLUMNS = 3
    POPUP_WIDE_COLUMNS = 4
    POPUP_WIDE_AFTER = 9

    def _ensure_more_popup(self) -> None:
        if self.more_popup is not None:
            return
        self.more_popup = QFrame(self, Qt.WindowType.Popup)
        self.more_popup.setObjectName("V2QuickTagMorePopup")
        self.more_popup.setFrameShape(QFrame.Shape.StyledPanel)
        outer = QVBoxLayout(self.more_popup)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(8)

        heading = QLabel("MORE TAGS", self.more_popup)
        heading.setProperty("role", "eyebrow")
        outer.addWidget(heading)

        # A fixed grid of equal cells, not a flow: ragged rows of unequal
        # buttons read as leftovers, and these are the same actions as the
        # rail's. The last cell is always the add control.
        self.more_grid = QGridLayout()
        self.more_grid.setContentsMargins(0, 0, 0, 0)
        self.more_grid.setHorizontalSpacing(6)
        self.more_grid.setVerticalSpacing(6)
        outer.addLayout(self.more_grid)

        self.more_add_button = QToolButton(self.more_popup)
        self.more_add_button.setObjectName("V2QuickTagPopupAdd")
        self.more_add_button.setProperty("quickTagAdd", "true")
        self.more_add_button.setText("+")
        self.more_add_button.setAccessibleName("Add or edit quick tags")
        self.more_add_button.setToolTip(
            "Add, edit, remove, or reorder quick tags")
        self.more_add_button.clicked.connect(self._add_from_popup)
        self.more_popup.hide()

    def _clear_more_grid(self) -> None:
        """Empty the grid, keeping the reusable add control alive."""
        while self.more_grid.count():
            item = self.more_grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is None:
                continue
            if widget is self.more_add_button:
                widget.setParent(self.more_popup)
                widget.hide()
                continue
            widget.setParent(None)
            widget.deleteLater()

    def _stock_more_popup(self, hidden: list[QPushButton]) -> None:
        """Rebuild the overflow as real tag buttons mirroring the rail's state."""
        if not hidden:
            self.more_buttons.clear()
            if self.more_popup is not None:
                # Empty the grid too. Leaving the last overflow's buttons in
                # place keeps dead widgets alive and puts the grid out of
                # step with more_buttons.
                self._clear_more_grid()
                self.more_popup.hide()
            return
        self._ensure_more_popup()
        self._clear_more_grid()
        self.more_buttons.clear()

        by_button = {self.buttons[tag.key]: tag for tag in self.tags
                     if tag.key in self.buttons}
        tags = [by_button[source] for source in hidden]

        columns = (self.POPUP_WIDE_COLUMNS
                   if len(tags) + 1 > self.POPUP_WIDE_AFTER
                   else self.POPUP_COLUMNS)
        # One width for every cell, taken from the widest label present, so
        # the grid lines up instead of stepping.
        cell = max(
            [self._natural_widths.get(tag.key, 52) for tag in tags] + [72])

        for index, (source, tag) in enumerate(zip(hidden, tags)):
            button = QPushButton(tag.label, self.more_popup)
            button.setProperty("quickTag", "true")
            button.setProperty(
                "tagFamily", source.property("tagFamily") or "neutral")
            button.ensurePolished()
            button.setFixedSize(cell, 32)
            button.setCheckable(True)
            button.setChecked(source.isChecked())
            button.setEnabled(source.isEnabled())
            button.setAccessibleName(f"Quick tag {tag.label}")
            button.setToolTip(f"{tag.description}. Saves immediately.")
            button.clicked.connect(
                lambda _checked=False, key=tag.key: self._overflow_clicked(key))
            self.more_grid.addWidget(
                button, index // columns, index % columns)
            self.more_buttons[tag.key] = button

        self.more_add_button.setFixedSize(cell, 32)
        self.more_grid.addWidget(
            self.more_add_button, len(tags) // columns, len(tags) % columns)
        self.more_add_button.show()

    def _add_from_popup(self) -> None:
        self._hide_more_popup()
        self.manage_requested.emit(True)

    def _overflow_clicked(self, key: str) -> None:
        # Close first: the tray restocks the popup as the clip's state
        # changes, and deleting the button under the cursor is not safe.
        self._hide_more_popup()
        self.tag_requested.emit(key)

    def _hide_more_popup(self) -> None:
        if self.more_popup is not None:
            self.more_popup.hide()

    def _show_more_popup(self) -> None:
        if self.more_popup is None:
            return
        self.more_popup.adjustSize()
        anchor = self.more_button.mapToGlobal(
            self.more_button.rect().topLeft())
        # Opens upward: the rail sits at the bottom of the player, so a
        # downward popup would fall off the window.
        self.more_popup.move(
            anchor.x() + self.more_button.width()
            - self.more_popup.width(),
            anchor.y() - self.more_popup.height() - 6)
        self.more_popup.show()
