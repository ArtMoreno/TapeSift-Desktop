"""Drawing on the film, and not drawing on it when no tool is armed.

The second half matters as much as the first: clicking the film returns
keyboard control to playback, and that must keep working. A tool left armed
would swallow those clicks with nothing on screen to explain why.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.models.telestration import MarkKind  # noqa: E402
from tapesift.ui_core.video_player import VideoPlayer  # noqa: E402
from tapesift.ui_v2.control_center import ControlCenterDeck  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def surface(qapp):
    """A player whose surface has a real frame rectangle to draw into."""
    owner = QWidget()
    owner_layout = QVBoxLayout(owner)
    player = VideoPlayer(AppSettings.load(), owner)
    deck = ControlCenterDeck(owner)
    player.attach_control_center(deck)
    owner_layout.addWidget(player)
    owner_layout.addWidget(deck)
    widget = player.video_widget
    widget.resize(960, 540)
    # Give it a frame so frame_rect() is populated, as it would be in use.
    image = QImage(1920, 1080, QImage.Format.Format_RGB32)
    image.fill(0x123456)
    widget._image = image
    widget._frame_rect = QRectF(0, 0, 960, 540)
    yield player, widget
    player.unload()
    owner.deleteLater()
    qapp.processEvents()


def _drag(widget, start, end, button=Qt.MouseButton.LeftButton):
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent

    def event(kind, pos):
        return QMouseEvent(kind, QPointF(*pos), QPointF(*pos), button,
                           button, Qt.KeyboardModifier.NoModifier)

    widget.mousePressEvent(event(QEvent.Type.MouseButtonPress, start))
    widget.mouseMoveEvent(event(QEvent.Type.MouseMove, end))
    widget.mouseReleaseEvent(event(QEvent.Type.MouseButtonRelease, end))


def test_no_tool_means_no_marks_and_click_still_reaches_playback(surface):
    player, widget = surface
    seen = []
    widget.clicked.connect(lambda: seen.append(True))

    _drag(widget, (100, 100), (400, 300))

    assert widget.marks() == []
    assert seen == [True], "clicking the film must still focus playback"


def test_dragging_with_the_arrow_tool_creates_one_mark(surface):
    _, widget = surface
    widget.set_tool("arrow")
    _drag(widget, (96, 54), (864, 486))

    marks = widget.marks()
    assert len(marks) == 1
    assert marks[0].kind is MarkKind.ARROW
    # 96/960 and 54/540 are a tenth in from each edge.
    assert marks[0].points[0] == pytest.approx((0.1, 0.1))
    assert marks[0].points[1] == pytest.approx((0.9, 0.9))


def test_a_click_without_a_drag_is_not_a_mark(surface):
    """Otherwise stray clicks leave invisible zero-length strokes behind."""
    _, widget = surface
    widget.set_tool("arrow")
    _drag(widget, (400, 300), (401, 300))
    assert widget.marks() == []


def test_the_tool_decides_the_kind(surface):
    _, widget = surface
    for tool, kind in (("arrow", MarkKind.ARROW),
                       ("line", MarkKind.LINE),
                       ("circle", MarkKind.CIRCLE)):
        widget.clear_marks()
        widget.set_tool(tool)
        _drag(widget, (200, 200), (600, 400))
        assert widget.marks()[0].kind is kind


def test_ink_is_recorded_with_the_mark(surface):
    _, widget = surface
    widget.set_tool("arrow")
    widget.set_ink("cyan")
    _drag(widget, (200, 200), (600, 400))
    assert widget.marks()[0].ink == "cyan"


def test_unknown_ink_falls_back_rather_than_storing_nonsense(surface):
    _, widget = surface
    widget.set_ink("chartreuse")
    assert widget.ink() == "gold"


def test_undo_removes_the_last_mark_only(surface):
    _, widget = surface
    widget.set_tool("arrow")
    _drag(widget, (100, 100), (300, 300))
    _drag(widget, (400, 100), (600, 300))
    assert len(widget.marks()) == 2

    assert widget.undo_mark() is True
    assert len(widget.marks()) == 1
    assert widget.undo_mark() is True
    assert widget.marks() == []
    assert widget.undo_mark() is False, "undo on nothing reports nothing done"


def test_clear_removes_everything(surface):
    _, widget = surface
    widget.set_tool("arrow")
    for _ in range(3):
        _drag(widget, (100, 100), (300, 300))
    assert widget.clear_marks() is True
    assert widget.marks() == []
    assert widget.clear_marks() is False


def test_marks_changed_fires_for_draw_undo_and_clear(surface):
    _, widget = surface
    fired = []
    widget.marksChanged.connect(lambda: fired.append(True))
    widget.set_tool("arrow")
    _drag(widget, (100, 100), (300, 300))
    widget.undo_mark()
    _drag(widget, (100, 100), (300, 300))
    widget.clear_marks()
    assert len(fired) == 4


def test_select_control_disarms_the_tool_and_restores_film_clicks(surface):
    """The persistent rail needs a visible way to stop drawing."""
    player, widget = surface
    # The rail is greyed until a play owns the strokes; arm it as selecting
    # a play does, then exercise the Select control.
    player.set_telestration_enabled(True)
    # Armed through the library: the rail no longer carries tool keys.
    player._shape_library_picked("route_arrow")
    assert widget.tool() == "route_arrow"

    player.tool_group.button(0).click()
    assert widget.tool() is None

    seen = []
    widget.clicked.connect(lambda: seen.append(True))
    _drag(widget, (100, 100), (400, 300))
    assert widget.marks() == []
    assert seen == [True]


def test_marks_can_be_loaded_back_onto_the_surface(surface):
    """Reopening a play puts its drawings back."""
    _, widget = surface
    widget.set_tool("arrow")
    _drag(widget, (96, 54), (864, 486))
    saved = widget.marks()

    widget.clear_marks()
    assert widget.marks() == []

    widget.set_marks(saved)
    assert len(widget.marks()) == 1
    assert widget.marks()[0].points[0] == pytest.approx((0.1, 0.1))


def test_drawing_outside_the_frame_clamps_into_it(surface):
    """Letterboxed film: a drag into the black bars stops at the picture."""
    _, widget = surface
    widget._frame_rect = QRectF(240, 0, 480, 540)   # pillarboxed
    widget.set_tool("arrow")
    _drag(widget, (0, 100), (900, 400))

    start, end = widget.marks()[0].points
    assert 0.0 <= start[0] <= 1.0 and 0.0 <= end[0] <= 1.0
    assert start[0] == pytest.approx(0.0)
    assert end[0] == pytest.approx(1.0)


def test_shape_library_offers_every_kind_and_ink(qapp_guard=None):
    """The picker must expose the whole library, not a curated subset."""
    from tapesift.models.telestration import INK, MarkKind
    from tapesift.ui_v2.shape_picker import SHAPE_GROUPS, ShapePicker

    picker = ShapePicker()
    listed = [name for _title, names in SHAPE_GROUPS for name in names]
    assert len(listed) == len(set(listed)), "a shape is listed twice"

    # Every listed shape is a real kind, and every ink is a real ink.
    for name in listed:
        assert MarkKind(name)
    assert set(picker.ink_buttons) <= set(INK)
    assert "neon" in picker.ink_buttons

    # Legacy duplicates (arrow/line/circle/freehand) stay off the grid; the
    # rail already carries them, and two buttons for one tool would confuse
    # which is armed.
    assert len(picker.shape_buttons) == len(MarkKind) - 4

    chosen = []
    picker.shape_chosen.connect(chosen.append)
    picker.shape_buttons["block_tee"].click()
    assert chosen == ["block_tee"]
    assert picker.shape_buttons["block_tee"].isChecked()


def test_library_is_the_only_way_to_arm_a_shape(surface):
    """The rail carries Select and the library, and nothing else arms.

    The three legacy keys (arrow/line/circle) were an arbitrary subset of
    the twenty-seven shapes, so they are gone; Select still disarms.
    """
    from PySide6.QtWidgets import QToolButton

    player, widget = surface
    player.set_telestration_enabled(True)

    names = {b.objectName()
             for b in player.telestration_rail.findChildren(QToolButton)}
    assert names == {"TelestrationSelect", "TelestrationLibrary",
                     "TelestrationInk", "TelestrationUndo",
                     "TelestrationClear"}

    player._open_shape_library()
    player.shape_picker.shape_buttons["zone_box"].click()
    assert widget.tool() == "zone_box"
    assert player.shape_library_button.isChecked()

    # Select disarms whatever the library armed.
    player.tool_group.button(0).click()
    assert widget.tool() is None
    assert not player.shape_library_button.isChecked()


def test_library_ink_reaches_the_surface_and_the_rail_swatch(surface):
    """Every ink must reach the stroke and be shown on the rail."""
    player, widget = surface
    player.set_telestration_enabled(True)
    player._open_shape_library()

    for ink in ("neon", "gold", "blue", "white"):
        player.shape_picker.ink_buttons[ink].click()
        assert widget.ink() == ink
        # One swatch, so it must always name the ink actually in use.
        assert player.ink_button.property("inkColor") == ink


def test_quick_tag_divider_splits_play_from_result():
    """The rail divider is derived from what each tag records."""
    from tapesift.ui_v2.quick_tag_tray import QUICK_TAGS, tag_group

    by_key = {tag.key: tag for tag in QUICK_TAGS}
    for key in ("run", "pass", "screen", "rpo_run", "rpo_pass"):
        assert tag_group(by_key[key]) == "play"
    # INT also sets run_pass, but it is reached for as an outcome.
    for key in ("interception", "touchdown", "first_down", "sack"):
        assert tag_group(by_key[key]) == "result"
