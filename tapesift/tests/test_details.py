from pathlib import Path

from tapesift.models.clip import Clip
from tapesift.services.detail_service import (
    compose_clip_name, details_to_tags, merge_tags, opponent_from_filename,
)
from tapesift.services.result_service import (
    add_results, has_result, join_results, primary_result, split_results, toggle_result,
)
from tapesift.services.project_service import ProjectSession

FULL = {
    "quarter": "2",
    "down_distance": "3rd & 7",
    "ball_on": "35",
    "play_type": "Inside Zone",
    "off_formation": "Gun Trips",
    "off_personnel": "11",
    "def_formation": "Nickel",
    "def_personnel": "4-2-5",
    "result": "Completion",
    "player_name": "J. Smith",
}


class TestComposeName:
    def test_quarterback_names_follow_involvement_and_preserve_custom_titles(self):
        from tapesift.services.detail_service import compose_auto_name, refresh_generated_title
        details = {"quarterback": "Darian Mensah", "player_name": "Malachi Toney", "run_pass": "Pass"}
        name = compose_auto_name(details, details_to_tags(details))
        assert name == "Malachi Toney · QB Darian Mensah - Pass"
        assert compose_clip_name({**details, "player_name": ""}) == "Darian Mensah - Pass"
        assert compose_clip_name({**details, "player_name": "darian mensah"}) == "darian mensah - Pass"
        assert "QB" not in compose_clip_name({**details, "run_pass": "Run"})
        assert "QB Darian Mensah" in compose_clip_name({**details, "run_pass": "", "result": "Sack"})
        assert "QB Darian Mensah" in compose_clip_name({**details, "run_pass": "Run", "play_type": "Scramble"})
        custom = Clip(0, 1000, clip_title="My custom title", details=details)
        refresh_generated_title(custom)
        assert custom.clip_title == "My custom title"

    def test_full_details_leads_with_the_player(self):
        assert compose_clip_name(FULL) == ("J. Smith - Q2 3rd & 7 on 35 "
                                           "Gun Trips 11 vs Nickel 4-2-5 "
                                           "Inside Zone - Completion")

    def test_player_and_action_lead(self):
        """The headline case: who did what, then the situation."""
        assert compose_clip_name({
            "player_name": "Damon Wilson", "action": "Pressure",
            "quarter": "2", "down_distance": "3rd & 7", "ball_on": "35",
        }) == "Damon Wilson Pressure - Q2 3rd & 7 on 35"

    def test_action_without_player(self):
        assert compose_clip_name({"action": "Missed Tackle",
                                  "quarter": "3"}) == "Missed Tackle - Q3"

    def test_result_dropped_when_it_repeats_the_action(self):
        name = compose_clip_name({"player_name": "Jones", "action": "Sack",
                                  "result": "sack"})
        assert name == "Jones Sack"

    def test_run_pass_included(self):
        assert compose_clip_name({"player_name": "Jones", "run_pass": "Pass",
                                  "play_type": "Screen"}) == \
            "Jones - Pass Screen"

    def test_play_type_only(self):
        assert compose_clip_name({"play_type": "Screen"}) == "Screen"

    def test_play_action_is_an_independent_naming_modifier(self):
        assert compose_clip_name({
            "run_pass": "Pass",
            "play_action": "Play Action",
        }) == "Pass Play Action"

    def test_quarter_already_prefixed(self):
        assert compose_clip_name({"quarter": "Q3"}).startswith("Q3")

    def test_partial_details(self):
        assert compose_clip_name({"down_distance": "1st & 10", "result": "TD"}) == \
            "1st & 10 - TD"

    def test_player_only(self):
        assert compose_clip_name({"player_name": "Jones"}) == "Jones"

    def test_empty(self):
        assert compose_clip_name({}) == ""
        assert compose_clip_name({"quarter": "  "}) == ""

    def test_name_is_filename_safe_after_sanitization(self):
        from tapesift.services.filename_service import sanitize_filename_base
        assert sanitize_filename_base(compose_clip_name(FULL))


class TestDetailsToTags:
    def test_values_in_field_order(self):
        tags = details_to_tags({"result": "TD", "quarter": "2", "player_name": "Jones"})
        assert tags == ["2", "TD", "Jones"]

    def test_play_type_becomes_tag(self):
        assert "Inside Zone" in details_to_tags(FULL)

    def test_play_action_becomes_tag(self):
        assert "Play Action" in details_to_tags({
            "play_action": "Play Action"})

    def test_other_players_each_become_a_tag(self):
        """Every player on a play is findable, not just the highlighted one."""
        tags = details_to_tags({
            "player_name": "Damon Wilson",
            "other_players": "Rueben Bain, Akheem Mesidor;  Tyler Baron ",
        })
        assert "Damon Wilson" in tags
        assert "Rueben Bain" in tags
        assert "Akheem Mesidor" in tags
        assert "Tyler Baron" in tags

    def test_multiple_results_become_independent_searchable_tags(self):
        tags = details_to_tags({
            "result": "Reception; First Down; Penalty",
        })
        assert tags == ["Reception", "First Down", "Penalty"]


class TestMultiResultSet:
    def test_legacy_single_result_is_already_compatible(self):
        assert split_results("Reception") == ["Reception"]

    def test_add_and_toggle_preserve_order_and_other_results(self):
        stored = add_results("Reception", "First Down", "Reception")
        assert stored == "Reception; First Down; Completion"
        assert toggle_result(stored, "Reception") == "First Down"

    def test_case_variants_do_not_duplicate(self):
        assert add_results("Touchdown", "touchdown") == "Touchdown"
        assert has_result("First Down", "first down")
        assert add_results("TD", "Touchdown") == "TD"
        assert split_results("TD; Touchdown") == ["TD", "Touchdown"]
        assert join_results(("TD", "Touchdown")) == "TD; Touchdown"
        assert has_result("TD", "Touchdown")
        assert toggle_result("TD; Touchdown; Sack", "Touchdown") == "Sack"

    def test_timeline_projection_uses_one_priority_result(self):
        assert primary_result("Reception; First Down") == "First Down"
        assert primary_result("Reception; Penalty Accepted") == "Penalty"


class TestOpponentFromFilename:
    def test_parses_all22_naming(self):
        assert opponent_from_filename(
            "OREGON O VS. IOWA D.mp4", "Oregon") == "Iowa"

    def test_works_from_either_side(self):
        assert opponent_from_filename(
            "MICHIGAN O VS. OREGON D.mp4", "Oregon") == "Michigan"

    def test_blank_without_a_team_to_compare(self):
        assert opponent_from_filename("OREGON O VS. IOWA D.mp4") == ""

    def test_blank_when_unparseable(self):
        assert opponent_from_filename("random film.mp4", "Oregon") == ""

    def test_blank_when_our_team_is_absent(self):
        assert opponent_from_filename(
            "DUKE O VS. WAKE FOREST D.mp4", "Oregon") == ""

    def test_merge_dedupes_case_insensitively(self):
        assert merge_tags(["nickel", "TD"], ["Nickel", "Jones"]) == \
            ["nickel", "TD", "Jones"]


class TestFixedDropdownSettings:
    def test_round_trip(self, tmp_path: Path):
        from tapesift.core.config import AppSettings
        settings = AppSettings()
        settings.use_fixed_dropdowns = True
        settings.fixed_tags = ["Pass TD", "Catch"]
        settings.fixed_details = {
            "quarter": ["Q1", "Q2"], "play_type": ["Jet Sweep"]}
        target = tmp_path / "settings.json"
        settings.save(target)
        loaded = AppSettings.load(target)
        assert loaded.use_fixed_dropdowns is True
        assert loaded.fixed_tags == ["Pass TD", "Catch"]
        assert "Q1" in loaded.fixed_details["quarter"]
        assert "Jet Sweep" in loaded.fixed_details["play_type"]
        assert "Run" not in loaded.fixed_details["play_type"]

    def test_defaults_include_football_lists(self):
        from tapesift.core.config import AppSettings
        settings = AppSettings()
        assert settings.use_fixed_dropdowns is True
        assert "Q1" in settings.fixed_details["quarter"]
        assert "Pass TD" in settings.fixed_tags
        assert "Screen" in settings.fixed_details["play_type"]
        assert "Run" not in settings.fixed_details["play_type"]


class TestDetailsPersistence:
    def test_details_survive_reopen(self, tmp_path: Path):
        session = ProjectSession.create("Details", tmp_path, tmp_path / "out")
        session.add_clip(Clip(start_ms=0, end_ms=1000, details=dict(FULL)))
        session.save()
        db_path = session.db_path
        session.conn.close()

        reopened = ProjectSession.open(db_path)
        assert reopened.clips[0].details == FULL
        reopened.conn.close()

    def test_duplicate_copies_details(self, tmp_path: Path):
        session = ProjectSession.create("Details2", tmp_path, tmp_path / "out")
        original = session.add_clip(Clip(start_ms=0, end_ms=1000,
                                         clip_title="X", details=dict(FULL)))
        dup = session.duplicate_clip(original.id)
        assert dup.details == FULL
        assert dup.details is not original.details
        session.conn.close()
