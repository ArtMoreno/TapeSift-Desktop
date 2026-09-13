"""Football vocabulary: unknown fallback, result roll-up, derived flags."""

from tapesift.core.config import AppSettings
from tapesift.services.football_vocab import (
    ACTION_KEY, DEFAULT_EXPLOSIVE_PASS_YARDS, DEFAULT_EXPLOSIVE_RUSH_YARDS,
    KIND_INTEGER, UNKNOWN_CATEGORY, VOCAB_FIELDS, YARDS_KEY, category_for,
    default_fixed_details, field_kind, heal_fixed_details, is_explosive,
    is_successful, lookup, parse_yards, values_for,
)
from tapesift.services.detail_service import DETAIL_FIELDS, details_to_tags
from tapesift.services.result_service import primary_result
from tapesift.ui_v2.attribute_grid import EDIT_CHOICES
from tapesift.ui_v2.quick_tag_tray import QUICK_TAGS


class TestUnknownFallback:
    def test_unknown_play_type_is_other_not_run_or_pass(self):
        assert category_for("play_type", "Wildcat") == UNKNOWN_CATEGORY
        assert category_for("play_type", "Wildcat") not in {"Run", "Pass"}

    def test_unknown_result_is_other(self):
        assert category_for("result", "TD") == UNKNOWN_CATEGORY
        assert category_for("result", "Drive Killer") == UNKNOWN_CATEGORY

    def test_blank_is_other_not_a_family(self):
        assert category_for("run_pass", "") == UNKNOWN_CATEGORY
        assert category_for("run_pass", "  ") == UNKNOWN_CATEGORY

    def test_known_values_match_case_insensitively(self):
        assert category_for("result", "touchdown") == "Score"
        assert lookup("result", " TOUCHDOWN ").canonical == "Touchdown"

    def test_unknown_still_tags(self):
        tags = details_to_tags({"play_type": "Wildcat", "result": "TD"})
        assert "Wildcat" in tags
        assert "TD" in tags


class TestResultRollup:
    def test_first_down_outranks_reception(self):
        assert primary_result("Reception; First Down") == "First Down"

    def test_penalty_outranks_reception(self):
        assert primary_result("Reception; Penalty Accepted") == "Penalty"

    def test_scoring_results_share_one_legend_category(self):
        assert primary_result("Touchdown") == "Score"
        assert primary_result("Field Goal Good") == "Score"
        assert primary_result("Touchdown; Reception; First Down") == "Score"

    def test_unknown_result_projects_to_other(self):
        assert primary_result("Made Up Thing") == UNKNOWN_CATEGORY

    def test_empty_result_projects_to_nothing(self):
        assert primary_result("") == ""


class TestAxesAreDisjoint:
    def test_play_type_does_not_offer_run_pass_or_kicks(self):
        play_types = {value.casefold() for value in values_for("play_type")}
        for banned in ("run", "pass", "play action", "punt", "field goal",
                       "kickoff"):
            assert banned not in play_types

    def test_action_does_not_offer_outcomes(self):
        actions = {value.casefold() for value in values_for(ACTION_KEY)}
        assert {'catch', 'run', 'one-handed catch', 'big hit', 'contested catch',
                'diving catch', 'sideline catch', 'stiff arm', 'juke', 'spin move', 'hurdle'} <= actions
        for banned in ("sack", "reception", "interception", "touchdown",
                       "explosive"):
            assert banned not in actions

    def test_result_does_not_offer_explosive_or_short_spellings(self):
        results = {value.casefold() for value in values_for("result")}
        for banned in ("explosive", "td", "int"):
            assert banned not in results


class TestDerivation:
    def test_no_play_cannot_be_successful_from_retained_yards(self):
        details = {"run_pass": "No Play", "down_distance": "1st & 10", "yards": "10"}
        assert is_successful(details) is None
        assert not is_explosive(details)
        details.update(run_pass="Run", result="Gain; Penalty Accepted")
        assert is_successful(details) is True

    def test_explosive_uses_family_thresholds(self):
        assert is_explosive(
            {"run_pass": "Run", "yards": "12"},
            DEFAULT_EXPLOSIVE_RUSH_YARDS, DEFAULT_EXPLOSIVE_PASS_YARDS)
        assert not is_explosive({"run_pass": "Run", "yards": "11"})
        assert is_explosive({"run_pass": "Pass", "yards": "16"})
        assert not is_explosive({"run_pass": "Pass", "yards": "15"})
        assert not is_explosive({"run_pass": "Special", "yards": "40"})

    def test_explosive_never_classifies_missing_yards(self):
        assert not is_explosive({"run_pass": "Run"})
        assert parse_yards({"yards": "abc"}) is None

    def test_standard_down_success(self):
        assert is_successful({"down_distance": "1st & 10", "yards": "5"}) is True
        assert is_successful({"down_distance": "1st & 10", "yards": "4"}) is False
        assert is_successful({"down_distance": "2nd & 10", "yards": "7"}) is True
        assert is_successful({"down_distance": "2nd & 10", "yards": "6"}) is False
        assert is_successful({"down_distance": "3rd & 5", "yards": "5"}) is True
        assert is_successful({"down_distance": "3rd & 5", "yards": "4"}) is False
        assert is_successful({"down_distance": "4th & 1", "yards": "1"}) is True

    def test_goal_success_uses_score_or_ball_on(self):
        assert is_successful({
            "down_distance": "1st & Goal", "result": "Touchdown",
        }) is True
        assert is_successful({
            "down_distance": "1st & Goal", "yards": "3", "ball_on": "OPP 3",
        }) is True
        assert is_successful({
            "down_distance": "1st & Goal", "yards": "2", "ball_on": "OPP 3",
        }) is False
        assert is_successful({
            "down_distance": "1st & Goal", "yards": "2",
        }) is None

    def test_success_unknown_without_yards_or_down(self):
        assert is_successful({"down_distance": "1st & 10"}) is None
        assert is_successful({"yards": "7"}) is None

    def test_derived_flags_are_not_written_as_tags(self):
        tags = details_to_tags({
            "run_pass": "Run", "yards": "20", "down_distance": "1st & 10",
        })
        assert "20" not in tags
        assert "Explosive" not in tags


class TestSettingsAndEditors:
    def test_hidden_dropdown_values_stay_known_and_new_values_are_seeded(self, tmp_path):
        settings = AppSettings()
        settings.hidden_fixed_details = {"result": ["Targeting", "Kneel"]}
        settings.fixed_details["result"] = ["Custom finish"]
        target = tmp_path / "settings.json"
        settings.save(target)
        loaded = AppSettings.load(target)
        assert "Targeting" not in loaded.fixed_details["result"]
        assert "Kneel" not in loaded.fixed_details["result"]
        assert "False Start" in loaded.fixed_details["result"]
        assert "Custom finish" in loaded.fixed_details["result"]
        assert category_for("result", "Targeting") == "Penalty"
        assert category_for("result", "Kneel") == "Special"

    def test_common_penalties_are_results_and_keep_the_ruling_separate(self):
        for value in ("False Start", "Offensive Holding", "Defensive Holding",
                      "Defensive Pass Interference", "Targeting", "Offside"):
            assert category_for("result", value) == "Penalty"
            assert value not in values_for("action")
        from tapesift.services.result_service import add_results, remove_result
        stored = add_results("Completion; First Down", "Offside", "Penalty Declined")
        assert stored == "Completion; First Down; Offside; Penalty Declined"
        assert remove_result(stored, "Offside") == "Completion; First Down; Penalty Declined"

    def test_yards_is_an_integer_detail_field(self):
        assert field_kind(YARDS_KEY) == KIND_INTEGER
        assert YARDS_KEY in {key for key, _ in DETAIL_FIELDS}
        assert YARDS_KEY not in default_fixed_details()

    def test_fixed_dropdowns_default_on(self):
        settings = AppSettings()
        assert settings.use_fixed_dropdowns is True
        assert settings.fixed_details["play_type"] == values_for("play_type")
        assert "Run" not in settings.fixed_details["play_type"]
        assert "Touchdown" in settings.fixed_details["result"]
        assert "TD" not in settings.fixed_details["result"]

    def test_heal_keeps_extras_and_strips_removed_spellings(self):
        healed = heal_fixed_details({
            "play_type": ["Run", "Jet Sweep", "Screen"],
            "highlight": ["Sack", "Pressure", "Custom Act"],
            "result": ["TD", "Explosive", "Clock Runoff"],
        })
        assert "Run" not in healed["play_type"]
        assert "Jet Sweep" in healed["play_type"]
        assert "Screen" in healed["play_type"]
        assert "Sack" not in healed[ACTION_KEY]
        assert "Pressure" in healed[ACTION_KEY]
        assert "Custom Act" in healed[ACTION_KEY]
        assert "highlight" not in healed
        assert "TD" not in healed["result"]
        assert "Explosive" not in healed["result"]
        assert "Clock Runoff" in healed["result"]
        assert "Touchdown" in healed["result"]

    def test_edit_choices_and_quick_tags_write_canonical_spellings(self):
        offered = {field: {value.casefold() for value in values_for(field)}
                   for field in VOCAB_FIELDS}
        for _row, choices in EDIT_CHOICES.items():
            for label, writes in choices:
                for field, value in writes.items():
                    if not value.strip() or field not in offered:
                        continue
                    # Down chips write the down word; distance is filled in
                    # beside it by set_down_keeping_distance.
                    if field == "down_distance" and " & " not in value:
                        continue
                    assert value.casefold() in offered[field], (
                        f"{label} writes {field}={value!r} which is not "
                        f"in football_vocab")
        for tag in QUICK_TAGS:
            for field, value in tag.details:
                if field not in offered:
                    continue
                assert value.casefold() in offered[field], (
                    f"quick tag {tag.key} writes {field}={value!r}")
