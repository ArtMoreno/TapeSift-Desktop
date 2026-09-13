import pytest

from tapesift.models.clip import Clip
from tapesift.services.drive_suggestions import previous_clip, suggest


def prior(**details):
    return Clip(0, 5000, details={"ball_on": "OWN 30", "down_distance": "1st & 10", "run_pass": "Run", **details})


def test_spots_and_yards_fill_only_missing_fields():
    suggestion = suggest(prior(), {"ball_on": "OWN 38"})
    assert suggestion.previous_gain == 8
    assert suggestion.current == {"down_distance": "2nd & 2"}
    suggestion = suggest(prior(yards="6"), {})
    assert suggestion.previous_gain is None
    assert suggestion.current == {"ball_on": "OWN 36", "down_distance": "2nd & 4"}
    assert suggest(prior(yards="6"), {"ball_on": "OWN 36", "down_distance": "2nd & 4"}).current == {}
    assert suggest(prior(yards="6"), {"down_distance": "2nd"}).current["down_distance"] == "2nd & 4"
    assert suggest(prior(yards="6"), {"ball_on": "OWN 38"}).current == {}
    assert suggest(prior(), {"ball_on": "OWN 38", "down_distance": "1st & 10"}).previous_gain is None


@pytest.mark.parametrize("spot,gain,down_distance,expected", [
    ("OWN 45", "12", "2nd & 7", {"ball_on": "OPP 43", "down_distance": "1st & 10"}),
    ("OWN 30", "-3", "2nd & 4", {"ball_on": "OWN 27", "down_distance": "3rd & 7"}),
    ("OPP 12", "5", "1st & 5", {"ball_on": "OPP 7", "down_distance": "1st & Goal"}),
    ("OPP 7", "2", "1st & Goal", {"ball_on": "OPP 5", "down_distance": "2nd & Goal"}),
    ("OWN 45", "5", "1st & 10", {"ball_on": "50", "down_distance": "2nd & 5"}),
])
def test_direction_and_down_progression(spot, gain, down_distance, expected):
    assert suggest(prior(ball_on=spot, yards=gain, down_distance=down_distance), {}).current == expected


@pytest.mark.parametrize("result", ["Penalty", "Penalty Declined", "Interception", "Fumble", "Touchdown", "Safety", "No Play", "Punt"])
def test_ambiguous_or_drive_ending_results_do_not_fill(result):
    suggestion = suggest(prior(result=result), {"ball_on": "OWN 38"})
    assert suggestion.previous_gain is None and not suggestion.current


def test_breaks_incomplete_and_fourth_down():
    assert not suggest(prior(yards="6"), {"drive_start": "1"}).current
    assert suggest(prior(result="Incompletion", run_pass="Pass"), {"ball_on": "OWN 30"}).previous_gain == 0
    assert suggest(prior(result="Incompletion", run_pass="Pass"), {"ball_on": "OWN 38"}).previous_gain is None
    assert not suggest(prior(yards="2", down_distance="4th & 5"), {}).current
    assert not suggest(prior(yards="6"), {"result": "Penalty"}).current
    assert not suggest(prior(ball_on="30", yards="6"), {}).current
    a, b, c = Clip(0, 10), Clip(20, 30), Clip(40, 50)
    assert previous_clip([c, a, b], c) is b
    b.enabled = False
    assert previous_clip([a, b, c], c) is None
    assert previous_clip([a, c], a) is None
