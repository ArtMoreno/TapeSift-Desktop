"""Merged vocabulary contracts at the persistence and shared consumer seams."""
import csv
import json
from types import SimpleNamespace

from tapesift.models.clip import Clip
from tapesift.services import result_service as results
from tapesift.services.detail_service import sync_detail_tags
from tapesift.services.football_context import FieldContext, is_successful, parse_down_distance
from tapesift.services.heatmap_export import export_csv
from tapesift.services.heatmap_service import build_heatmap, result_colour
from tapesift.services.project_service import ProjectSession
from tapesift.ui_v2.tag_readout import OUTCOME_COLORS


def test_legacy_selection_equality_does_not_change_vocabulary_classification():
    assert results.has_result("TD", "Touchdown")
    assert results.add_results("TD", "Touchdown") == "TD"
    assert results.toggle_result("TD; Custom", "Touchdown") == "Custom"
    assert results.primary_result("TD") == "Other"
    assert results.primary_result("TD; Touchdown") == "Score"
    assert results.primary_result("Touchdown; TD") == "Score"
    assert results.primary_result("Sasquatch") == "Other"
    assert results.result_key("Custom INT note") == "custom int note"
    assert results.split_results("INT; Interception") == ["INT", "Interception"]
    for stored in ("TD; Touchdown", "Touchdown; TD"):
        assert results.add_results(stored, "Sack") == stored + "; Sack"
        assert results.primary_result(results.add_results(stored, "Sack")) == "Score"
    assert not build_heatmap([Clip(0, 1000, details={"result": "INT"})]).plays[0].turnover


def test_result_palette_covers_categories_and_legacy_custom_styles():
    for value, category in zip(
            ["Touchdown", "Interception", "Sack", "Reception", "First Down",
             "No Gain", "Penalty Accepted", "Punt Returned", "Sasquatch"],
            ["score", "turnover", "negative", "complete", "first_down",
             "stop", "penalty", "special", "other"]):
        assert result_colour(value) == OUTCOME_COLORS[category]
    assert result_colour("TD") == OUTCOME_COLORS["other"]
    assert result_colour("Interception", {"int": {"color": "#123456"}}) == "#123456"
    assert result_colour("First Down", {"1st_down": {"color": "#654321"}}) == "#654321"


def test_partial_situations_and_goal_success_remain_honest():
    for raw, expected in [("3 47", ("3", "47")), ("4th&G", ("4", "Goal")),
                          ("To Go Inches", ("", "Inches")),
                          ("2nd and inches", ("2", "Inches")),
                          ("To Go 1200", ("", "1200")),
                          ("2nd & -3", ("", "")), ("3 7 garbage", ("", ""))]:
        assert parse_down_distance(raw) == expected
    for ball, yards, expected in [("OPP 5", "5", True), ("OWN 5", "5", False),
                                   ("OWN 5", "95", True), ("5", "5", None)]:
        details = {"down_distance": "1st & Goal", "ball_on": ball, "yards": yards}
        before = dict(details)
        assert is_successful(details) is expected
        assert details == before
    assert is_successful({"down_distance": "3rd & Inches", "yards": "0"}) is None
    context = FieldContext.from_details({"ball_on": "OPP 5", "down_distance": "3rd & Inches",
        "offense_team_id": "a", "attack_direction": "right"}, ["a", "b"])
    assert context.los_yards == 95 and context.to_gain_yards is None


def test_mirrored_tag_cleanup_preserves_other_field_ownership_and_free_tags():
    previous = {"quarter": "Q1", "result": "First Down", "action": "Pressure"}
    current = {"quarter": "Q2", "action": "Pressure", "player_name": "First Down"}
    assert sync_detail_tags(["Q1", "1st down", "First Down", "Pressure", "free note"], previous, current) == [
        "First Down", "Pressure", "free note"]
    assert sync_detail_tags(["1st down"], {"result": "First Down"}, {"result": "1st down"}) == ["1st down"]


def test_heatmap_csv_json_keep_all_recorded_situation_fields(tmp_path):
    fields = dict(play_action="Play Action", down_distance="To Go Inches", ball_on="OPP 7",
                  yards="-3", action="Pressure", other_players="A; B",
                  offense_team_id="team:a", attack_direction="left", game_clock="0:00")
    clip = Clip(0, 1000, details={**fields, "run_pass": "Pass", "play_type": "RPO", "result": "Sack"})
    data = build_heatmap([clip])
    export_csv(data, tmp_path / "plays.csv")
    with (tmp_path / "plays.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    payload = json.loads(data.to_json())["plays"][0]
    for key, value in fields.items():
        assert row[key] == payload[key] == value
    assert clip.details == {**fields, "run_pass": "Pass", "play_type": "RPO", "result": "Sack"}
    assert build_heatmap([Clip(0, 1000)]).plays[0].yards == ""


def test_bulk_down_preserves_each_distance_and_undo(tmp_path):
    session = ProjectSession.create("Bulk QA", tmp_path, tmp_path / "out")
    try:
        originals = ["1st & 10", "To Go 7", "To Go Goal", "3rd & Inches", ""]
        clips = [Clip(i*1000, i*1000+900, details={"down_distance": value}, tags=[value, "free"])
                 for i, value in enumerate(originals)]
        session.add_clips(clips)
        assert session.apply_details_to_clips([c.id for c in clips], {"down_distance": "2nd & 10"},
                                              preserve_distance=True) == 5
        assert [c.details["down_distance"] for c in session.clips] == [
            "2nd & 10", "2nd & 7", "2nd & Goal", "2nd & Inches", "2nd"]
        assert all("free" in c.tags for c in session.clips)
        session.undo()
        assert [c.details["down_distance"] for c in session.clips] == originals
    finally:
        session.close()


def test_typed_yards_route_never_overwrites_down_distance(monkeypatch):
    from tapesift.ui_core.main_window_workflow import MainWindowWorkflow, QInputDialog
    clip = Clip(0, 1000, details={"down_distance": "2nd & 7", "yards": "3"})
    edits = []
    controller = SimpleNamespace(session=SimpleNamespace(get_clip=lambda _id: clip),
        select_clip=lambda *a, **kw: True,
        clip_editor=SimpleNamespace(apply_quick_details=edits.append))
    for entered, expected in [("-3", {"yards": "-3"}), ("", {"yards": ""}), ("oops", None)]:
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: (entered, True))
        before = len(edits)
        MainWindowWorkflow._grid_cell_typed(controller, "yards", clip.id)
        assert len(edits) == before + (expected is not None)
        if expected is not None:
            assert edits[-1] == expected
