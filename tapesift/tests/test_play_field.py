"""Field drawings must use recorded yards and placement must remain a draft."""
import pytest

from tapesift.services.play_field_service import describe_play, place


def details(**changes):
    return {"ball_on": "OWN 25", "down_distance": "1st & 10", "run_pass": "Pass",
            "result": "Completion; First Down", "yards": "18", "yac": "11", **changes}


def test_catch_yac_and_flip_share_measured_coordinates_without_mutation():
    values = details()
    original = dict(values)
    geometry = describe_play(values, [])
    assert (geometry.context.los_yards, geometry.context.to_gain_yards,
            geometry.catch, geometry.end) == (25, 35, 32, 43)
    flipped = describe_play({**values, "field_flip": "1"}, [])
    assert (flipped.catch, flipped.end) == (geometry.catch, geometry.end)
    assert flipped.flipped
    assert values == original
    screen = describe_play(details(ball_on="OWN 30", yards="12", yac="15"), [])
    assert (screen.catch, screen.end) == (27, 42)
    negative_yac = describe_play(details(yards="18", yac="-3"), [])
    assert negative_yac.catch == 46


@pytest.mark.parametrize("changes", [
    {"ball_on": "25"}, {"yards": ""}, {"yards": "200"},
    {"result": "Completion; Incompletion"}, {"result": "Sack", "yards": "5"},
    {"result": "No Gain", "yards": "3"}, {"result": "Touchdown", "yards": "3"},
    {"result": "First Down", "yards": "3"}, {"result": "Incompletion"},
    {"result": "No Play"}, {"run_pass": "No Play"},
    {"result": "Offensive Holding; Penalty Accepted"},
    {"result": "Interception"}, {"result": "Fumble Lost"},
])
def test_unknown_or_compound_results_do_not_invent_simple_endpoints(changes):
    geometry = describe_play(details(**changes), [])
    assert geometry.end is None
    assert geometry.catch is None


def test_turnover_and_enforcement_are_separate_from_credited_gain():
    intercepted = details(result="Interception", field_event_spot="48", field_return_end="40")
    geometry = describe_play(intercepted, [])
    assert (geometry.end, geometry.event, geometry.return_end) == (None, 48, None)
    intercepted["field_recovery_team"] = "defense"
    assert describe_play(intercepted, []).return_end == 40
    penalty = details(result="Offensive Holding; Penalty Accepted", field_enforced_spot="15")
    geometry = describe_play(penalty, [])
    assert geometry.end is None and geometry.enforced == 15
    assert describe_play(details(result="Completion; Penalty Declined"), []).end == 43


def test_batted_completion_and_zero_yard_sack_keep_their_recorded_outcomes():
    batted = details(result="Batted Pass; Completion; First Down")
    geometry = describe_play(batted, [])
    assert (geometry.catch, geometry.end) == (32, 43)
    assert place(batted, "finish", 44)["yards"] == "19"
    assert describe_play(details(result="Batted Pass; Incompletion"), []).end is None
    sack = describe_play(details(result="Sack", yards="0", yac=""), [])
    assert sack.end == 25 and not sack.passing
    assert describe_play(details(result="Sack; Completion", yards="0"), []).end is None
    assert describe_play(details(result="TFL", yards="0"), []).end is None
    assert describe_play(details(result="Field Goal Good"), []).end is None


def test_placement_updates_only_owned_measurements_and_never_saves():
    values = details(notes="preserve", field_hash="left")
    moved = place(values, "finish", 44)
    assert moved["yards"] == "19" and values["yards"] == "18"
    assert place(moved, "catch", 33)["yac"] == "11"
    assert place(values, "start", 71)["ball_on"] == "OPP 29"
    assert place(values, "start", 50)["ball_on"] == "50"
    assert moved["field_hash"] == "left" and moved["notes"] == "preserve"
    with pytest.raises(ValueError, match="Place the start"):
        place(details(ball_on=""), "finish", 40)
    with pytest.raises(ValueError, match="event spot"):
        place(details(result="Interception"), "finish", 40)
    with pytest.raises(ValueError, match="recovering team"):
        place(details(result="Interception"), "return", 40)


def test_native_field_hit_testing_flip_and_dialog_undo():
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from tapesift.ui_v3.play_field import PlayFieldWidget, PlayFieldDialog

    app = QApplication.instance() or QApplication([])
    field = PlayFieldWidget()
    field.resize(800, 230)
    field.set_details(details(), [])
    for flip in ("0", "1"):
        field.set_details(details(field_flip=flip), [])
        for yard in (0, 25, 50, 75, 100):
            anchor = field._point(yard, .38, QRectF(field.rect()))
            assert field.yard_at(anchor) == yard
    assert field.yard_at(QPointF(-5, -5)) is None
    image = field.to_image(1000, 280)
    assert image.width() == 1000 and not image.isNull()
    dialog = PlayFieldDialog(details(), [])
    changes = []
    dialog.details_changed.connect(changes.append)
    dialog.show()
    app.processEvents()
    dialog.mode.setCurrentIndex(dialog.mode.findData("finish"))
    anchor = dialog.field._point(45, .5, QRectF(dialog.field.rect()))
    QTest.mouseClick(dialog.field, Qt.MouseButton.LeftButton, pos=anchor.toPoint())
    assert dialog.details["yards"] == "20" and changes[-1]["yards"] == "20"
    QTest.mouseClick(dialog.flip, Qt.MouseButton.LeftButton)
    assert dialog.details["field_flip"] == "1" and dialog.details["yards"] == "20"
    QTest.mouseClick(dialog.undo, Qt.MouseButton.LeftButton)
    assert "field_flip" not in dialog.details
    QTest.mouseClick(dialog.undo, Qt.MouseButton.LeftButton)
    assert dialog.details["yards"] == "18"
    dialog.close()
    field.close()
