"""Disposable V3 acceptance through Details, Tag Map, save, reopen and undo."""
import csv
import json

from PySide6.QtWidgets import QApplication

from tapesift.core.config import AppSettings
from tapesift.models.clip import Clip
from tapesift.services import result_service
from tapesift.services.heatmap_export import export_csv
from tapesift.services.heatmap_service import build_heatmap
from tapesift.services.project_service import ProjectSession
from tapesift.ui_v2.attribute_grid import EDIT_CHOICES
from tapesift.ui_v3.main_window import MainWindowV3


SCENARIOS = [
    ("ordinary run", "Run", "Inside Zone", "", "Gain", "1st & 10", "OWN 25", "5"),
    ("RPO run", "Run", "RPO", "", "First Down", "2nd & 2", "OWN 25", "3"),
    ("RPO pass incomplete", "Pass", "RPO", "", "Incompletion", "1st & 10", "OWN 25", "0"),
    ("play action completion", "Pass", "", "Play Action", "Completion", "2nd & 7", "OWN 35", "12"),
    ("scramble", "Run", "Scramble", "", "Gain", "3rd & 8", "OWN 35", "6"),
    ("sack retained fumble", "Pass", "", "", "Sack; Fumble Recovered", "2nd & 7", "OWN 35", "-5"),
    ("sack lost fumble", "Pass", "", "", "Sack; Fumble Lost", "2nd & 7", "OWN 35", "-5"),
    ("interception", "Pass", "", "", "Interception", "3rd & 8", "OWN 35", ""),
    ("declined penalty", "Run", "", "", "Gain; Penalty Declined", "1st & 10", "OWN 35", "6"),
    ("nullified play", "No Play", "", "", "Penalty Accepted", "1st & 10", "OWN 35", ""),
    ("fourth down stop", "Run", "", "", "Turnover on Downs", "4th & 2", "OPP 10", "1"),
    ("goal to go TD", "Pass", "", "", "Touchdown", "2nd & Goal", "OPP 5", "5"),
    ("second and two inside ten", "Run", "", "", "Gain", "2nd & 2", "OPP 7", "1"),
    ("kneel", "Run", "", "", "Kneel", "1st & 10", "OWN 35", "-1"),
    ("false start", "No Play", "", "", "False Start; Penalty Accepted", "1st & 10", "OWN 35", ""),
    ("declined offside completion", "Pass", "Dropback", "", "Completion; First Down; Offside; Penalty Declined", "3rd & 4", "OWN 35", "8"),
    ("accepted holding", "Run", "Duo", "", "Offensive Holding; Penalty Accepted", "1st & 10", "OWN 35", ""),
]


def test_logging_scenarios_through_both_v3_edit_routes(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = AppSettings(onboarding_seen=True)
    window = MainWindowV3(settings, workspace_state_path=tmp_path / "workspace.json")
    session = ProjectSession.create("Vocabulary acceptance", tmp_path / "projects", tmp_path / "out")
    try:
        clips = [Clip(i*2000, i*2000+1500, clip_title=spec[0], clip_number=i+1,
                      details={"custom": "  preserve  "}, tags=["free note"])
                 for i, spec in enumerate(SCENARIOS)]
        session.add_clips(clips)
        session.save()
        assert window._activate_session(session)
        window.resize(1708, 921)
        window.show()
        window.player._duration_changed(len(clips)*2000)
        app.processEvents()
        for clip, spec in zip(clips, SCENARIOS):
            _, family, concept, modifier, outcome, situation, ball, yards = spec
            assert window.select_clip(clip.id, seek=False)
            editor = window.clip_editor
            fields = dict(run_pass=family, play_type=concept, play_action=modifier,
                          result=outcome, down_distance=situation, ball_on=ball, yards=yards,
                          action="Pressure", quarter="Q2", other_players="A; B")
            for field, value in fields.items():
                editor.detail_edits[field].setText(value)
            assert editor._apply(), editor.error_label.text()
            saved = dict(session.get_clip(clip.id).details)
            expected = dict(fields)
            # Entering numeric yardage also records its gain/loss result.
            if yards:
                expected["result"] = result_service.add_results(outcome, "Gain" if int(yards) > 0 else "Loss" if int(yards) < 0 else "No Gain")
            assert {field: saved.get(field, "") for field in fields} == expected
            assert saved["custom"] == "  preserve  "
            session.save()
            reopened = ProjectSession.open(session.db_path)
            try:
                assert reopened.get_clip(clip.id).details == saved
            finally:
                reopened.close()
            # Exercise the V3 Tag Map result route, which toggles through the
            # same editor checkpoint and persistence path as a real grid choice.
            index = next(i for i, (_, values) in enumerate(EDIT_CHOICES["result"])
                         if values["result"] == "Reception")
            window._grid_cell_choice_picked(clip.id, "result", index)
            edited = session.get_clip(clip.id)
            assert result_service.has_result(edited.details["result"], "Reception")
            assert "free note" in edited.tags
            grid = window.player.attribute_grid
            grid.resize(1000, 346)
            window.player.slider.fit_range(0, len(clips)*2000)
            span = grid.clip_span(edited)
            window.player.slider.fit_range(clip.start_ms, clip.end_ms)
            assert grid.clip_span(edited)[1] - grid.clip_span(edited)[0] > span[1] - span[0]
            assert all(value in grid.tooltip_for_clip(edited)
                       for value in result_service.split_results(edited.details["result"]))
            assert not result_service.has_result(edited.details["result"], "Incompletion")
            data = build_heatmap(session.clips)
            row = next(p for p in data.plays if p.clip_id == clip.id)
            assert row.result == edited.details["result"]
            session.save()
            reopened = ProjectSession.open(session.db_path)
            try:
                assert reopened.get_clip(clip.id).details == edited.details
            finally:
                reopened.close()
            window._undo()
            assert session.get_clip(clip.id).details == saved
            session.save()
        data = build_heatmap(session.clips)
        export_csv(data, tmp_path / "acceptance.csv")
        with (tmp_path / "acceptance.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        payload = json.loads(data.to_json())["plays"]
        for row, play in zip(rows, payload):
            for field in ("result", "play_action", "down_distance", "ball_on", "yards", "action", "other_players"):
                assert row[field] == play[field]
        app.processEvents()
    finally:
        if window.session is not None:
            window.session.save()
        window.close()

