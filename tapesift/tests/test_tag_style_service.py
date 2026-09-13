"""Project-level tag presentation stays separate from clip metadata."""

from tapesift.services.tag_style_service import (
    canonical_tag_key, normalized_styles, primary_timeline_tag, style_for_tag,
)


def test_tag_style_keys_ignore_case_and_punctuation():
    assert canonical_tag_key("  Pass TD  ") == "pass_td"
    assert canonical_tag_key("Pick-6") == "pick_6"


def test_unconfigured_tags_keep_existing_first_tag_behavior():
    assert primary_timeline_tag(["Pressure", "3rd Down"], {}) == (
        "pressure", "Pressure", "")


def test_secondary_or_hidden_tags_do_not_lead_primary_tag_timeline():
    styles = {
        "pressure": {"primary": False},
        "3rd_down": {"show_on_timeline": False},
        "sack": {"color": "#f59e0b"},
    }
    assert primary_timeline_tag(
        ["Pressure", "3rd Down", "Sack"], styles) == (
            "sack", "Sack", "#f59e0b")


def test_tag_category_and_custom_color_drive_timeline_key():
    styles = {
        "pass_td": {
            "color": "#c084fc",
            "category": "Scoring Play",
            "primary": True,
            "show_on_timeline": True,
        }
    }
    assert primary_timeline_tag(["Pass TD"], styles) == (
        "scoring_play", "Scoring Play", "#c084fc")


def test_style_normalization_drops_bad_colors_and_keeps_defaults():
    styles = normalized_styles({
        "Pass TD": {"color": "purple", "primary": 0},
        "": {"color": "#46e485"},
    })
    assert styles == {
        "pass_td": {
            "color": "", "category": "", "primary": False,
            "show_on_timeline": True,
        }
    }
    assert style_for_tag("Pass TD", styles)["primary"] is False
