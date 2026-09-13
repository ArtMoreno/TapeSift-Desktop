"""Marks live in film coordinates, not screen coordinates.

The whole point of these tests is one property: a mark drawn on a player
stays on that player, whatever the display does afterwards. Resize the
window, move to another monitor, export at 1080p something drawn on a
480-wide preview, or zoom in to look at the right guard - the mark has to
survive all of it, and storing widget pixels survives none of it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tapesift.models.telestration import (
    DEFAULT_INK, INK, Mark, MarkKind, marks_from_json, marks_to_json,
    to_film, to_widget)


@dataclass
class Rect:
    """Stand-in for QRectF, so the model can be tested without Qt."""
    _x: float
    _y: float
    _w: float
    _h: float

    def x(self): return self._x
    def y(self): return self._y
    def width(self): return self._w
    def height(self): return self._h


# A 16:9 frame letterboxed inside a wider widget - the normal case.
LETTERBOXED = Rect(267.0, 0.0, 427.0, 240.0)


def test_centre_of_the_frame_is_a_half_in_both_axes():
    assert to_film(267.0 + 427.0 / 2, 120.0, LETTERBOXED) == (0.5, 0.5)


def test_corners_map_to_the_unit_square():
    assert to_film(267.0, 0.0, LETTERBOXED) == (0.0, 0.0)
    assert to_film(267.0 + 427.0, 240.0, LETTERBOXED) == (1.0, 1.0)


def test_round_trip_is_stable():
    for point in ((300.0, 40.0), (500.0, 120.0), (690.0, 239.0)):
        fx, fy = to_film(*point, LETTERBOXED)
        back = to_widget(fx, fy, LETTERBOXED)
        assert back[0] == pytest.approx(point[0], abs=1e-6)
        assert back[1] == pytest.approx(point[1], abs=1e-6)


def test_a_mark_survives_the_window_being_resized():
    """The property the whole design exists for."""
    small = Rect(267.0, 0.0, 427.0, 240.0)
    drawn_at = (400.0, 90.0)
    fx, fy = to_film(*drawn_at, small)

    # Same film, same 16:9 aspect, twice the size on a bigger window.
    large = Rect(534.0, 0.0, 854.0, 480.0)
    moved = to_widget(fx, fy, large)

    # It lands at the same place *in the picture*, which is what matters.
    assert (moved[0] - large.x()) / large.width() == pytest.approx(fx)
    assert (moved[1] - large.y()) / large.height() == pytest.approx(fy)


def test_a_mark_drawn_on_a_preview_lands_correctly_at_export_size():
    """Drawn on a 480-wide preview, rendered into a 1920-wide export."""
    preview = Rect(0.0, 0.0, 480.0, 270.0)
    export = Rect(0.0, 0.0, 1920.0, 1080.0)
    fx, fy = to_film(120.0, 67.5, preview)          # a quarter in, a quarter down
    assert (fx, fy) == pytest.approx((0.25, 0.25))
    assert to_widget(fx, fy, export) == pytest.approx((480.0, 270.0))


def test_drawing_outside_the_frame_clamps_to_its_edge():
    """A drag off the picture stops at the edge.

    Otherwise the mark is stored somewhere that can never be displayed
    again, which reads to the analyst as the drawing vanishing.
    """
    assert to_film(0.0, -50.0, LETTERBOXED) == (0.0, 0.0)
    assert to_film(9999.0, 9999.0, LETTERBOXED) == (1.0, 1.0)


def test_a_zero_sized_rect_does_not_divide_by_zero():
    """Frames arrive before the widget has been laid out."""
    empty = Rect(0.0, 0.0, 0.0, 0.0)
    x, y = to_film(10.0, 10.0, empty)
    assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0


def test_marks_round_trip_through_json():
    marks = [
        Mark(MarkKind.ARROW, [(0.3, 0.62), (0.78, 0.26)], ink="gold"),
        Mark(MarkKind.CIRCLE, [(0.2, 0.5), (0.28, 0.58)], ink="cyan"),
    ]
    restored = marks_from_json(marks_to_json(marks))
    assert len(restored) == 2
    assert restored[0].kind is MarkKind.ARROW
    assert restored[0].points[1] == pytest.approx((0.78, 0.26))
    assert restored[1].ink == "cyan"
    assert restored[0].id == marks[0].id


def test_malformed_entries_are_skipped_not_fatal():
    """One bad drawing must not stop a project opening."""
    restored = marks_from_json([
        {"kind": "arrow", "points": [[0.1, 0.1], [0.9, 0.9]]},
        {"kind": "arrow", "points": []},              # no geometry
        {"kind": "nonsense", "points": [[0.2, 0.2], [0.4, 0.4]]},
        "not a dict",
        {"points": [[0.5, 0.5], [0.6, 0.6]]},         # missing kind
    ])
    kinds = [m.kind for m in restored]
    assert len(restored) == 3
    assert kinds[1] is MarkKind.ARROW      # unknown kind falls back
    assert kinds[2] is MarkKind.ARROW      # missing kind falls back


def test_unknown_ink_falls_back_to_the_default():
    mark = Mark.from_dict(
        {"kind": "arrow", "ink": "chartreuse",
         "points": [[0.1, 0.1], [0.2, 0.2]]})
    assert mark.ink == DEFAULT_INK
    assert mark.ink in INK


def test_non_list_payload_is_tolerated():
    assert marks_from_json(None) == []
    assert marks_from_json({}) == []
    assert marks_from_json("[]") == []


@pytest.mark.parametrize("bad_point", [
    [float("nan"), 0.2],
    [float("inf"), 0.2],
    [-0.01, 0.2],
    [1.01, 0.2],
    [0.2],
    ["0.2", 0.2],
])
def test_one_bad_required_point_rejects_the_whole_mark(bad_point):
    payload = [{
        "kind": "arrow",
        "points": [[0.1, 0.1], bad_point, [0.8, 0.8]],
    }]
    assert marks_from_json(payload) == []


@pytest.mark.parametrize("kind", ["arrow", "line", "circle"])
def test_geometric_marks_require_two_points(kind):
    assert marks_from_json([{
        "kind": kind,
        "points": [[0.5, 0.5]],
    }]) == []


def test_freehand_keeps_all_valid_points_without_geometric_truncation():
    restored = marks_from_json([{
        "kind": "freehand",
        "points": [[0.1, 0.1], [0.2, 0.3], [0.4, 0.5]],
    }])
    assert len(restored) == 1
    assert restored[0].kind is MarkKind.FREEHAND
    assert restored[0].points == [
        (0.1, 0.1), (0.2, 0.3), (0.4, 0.5)]


def test_freehand_preserves_its_legacy_single_sample_semantics():
    restored = marks_from_json([{
        "kind": "freehand",
        "points": [[0.25, 0.75]],
    }])
    assert len(restored) == 1
    assert restored[0].kind is MarkKind.FREEHAND
    assert restored[0].points == [(0.25, 0.75)]


@pytest.mark.parametrize("bad_width", [float("nan"), float("inf"), -1, 0])
def test_nonfinite_or_nonpositive_width_rejects_the_whole_mark(bad_width):
    assert marks_from_json([{
        "kind": "line",
        "points": [[0.1, 0.2], [0.8, 0.9]],
        "width": bad_width,
    }]) == []


def test_every_kind_has_a_style_and_survives_a_json_round_trip():
    """A new tool is a table entry, and it must persist like any other."""
    from tapesift.models.telestration import (
        INK, SHAPES, Mark, MarkKind, marks_from_json, marks_to_json)

    assert set(SHAPES) == set(MarkKind), "every kind needs a draw style"

    marks = [Mark(kind, [(0.2, 0.3), (0.7, 0.8)], ink="neon")
             for kind in MarkKind]
    restored = marks_from_json(marks_to_json(marks))
    assert [m.kind for m in restored] == list(MarkKind)
    assert all(m.ink == "neon" for m in restored)

    # Neon green and the basic colours are all selectable inks.
    for name in ("neon", "white", "black", "orange", "yellow", "green",
                 "blue", "purple", "pink", "gold", "cyan", "red"):
        assert name in INK


def test_legacy_kinds_and_inks_still_load():
    """Projects saved before the shape library must open unchanged."""
    from tapesift.models.telestration import MarkKind, marks_from_json

    legacy = [{"kind": k, "ink": "gold", "points": [[0.1, 0.1], [0.5, 0.5]]}
              for k in ("arrow", "circle", "line")]
    restored = marks_from_json(legacy)
    assert [m.kind for m in restored] == [
        MarkKind.ARROW, MarkKind.CIRCLE, MarkKind.LINE]

    # An unknown future kind degrades to an arrow rather than losing the mark.
    future = marks_from_json(
        [{"kind": "not_a_real_tool", "points": [[0.1, 0.1], [0.4, 0.4]]}])
    assert [m.kind for m in future] == [MarkKind.ARROW]
