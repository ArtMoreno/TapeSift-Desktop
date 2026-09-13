"""One player, however their name got typed - and never two merged into one."""

from __future__ import annotations

import pytest

from tapesift.services.player_name_service import (
    name_parts, resolve_roster, resolved_key)
from tapesift.services.tag_style_service import canonical_tag_key


def _groups(names: list[str]) -> list[set[str]]:
    """The spellings, gathered by the player they resolved to."""
    roster = resolve_roster(names)
    gathered: dict[str, set[str]] = {}
    for key, display in roster.items():
        gathered.setdefault(display, set()).add(key)
    return list(gathered.values())


class TestParts:
    @pytest.mark.parametrize("name,expected", [
        ("Mark Fletcher", ("mark", "fletcher")),
        ("mark  fletcher", ("mark", "fletcher")),
        ("M. Fletcher", ("m", "fletcher")),
        ("Fletcher", ("", "fletcher")),
        ("Fletcher, Mark", ("mark", "fletcher")),
        ("Mark Fletcher Jr.", ("mark", "fletcher")),
        ("", ("", "")),
    ])
    def test_a_name_reads_the_same_however_it_is_written(self, name, expected):
        assert name_parts(name) == expected


class TestMerging:
    def test_five_spellings_are_one_player(self):
        assert len(_groups([
            "Mark Fletcher", "mark fletcher", "MARK FLETCHER",
            "M. Fletcher", "Fletcher", "Mark Fletcher Jr."])) == 1

    def test_reversed_order_is_the_same_player(self):
        assert len(_groups(["Fletcher, Mark", "Mark Fletcher"])) == 1

    def test_the_name_shown_is_the_one_a_person_would_say(self):
        """A roster sorts one way; a screen should read the other."""
        roster = resolve_roster(["Fletcher, Mark", "Mark Fletcher"])
        assert set(roster.values()) == {"Mark Fletcher"}

    def test_case_alone_was_already_handled_and_still_is(self):
        assert len(_groups(["CharMar Brown", "charmar brown"])) == 1


class TestNotMerging:
    """Merging two real players is the error worth avoiding."""

    def test_different_first_names_stay_apart(self):
        assert len(_groups(["Mark Fletcher", "Dale Fletcher"])) == 2

    def test_different_initials_stay_apart(self):
        assert len(_groups(["M. Fletcher", "D. Fletcher"])) == 2

    def test_different_surnames_never_touch(self):
        assert len(_groups(["Damari Brown", "CharMar Brown"])) == 2

    def test_a_bare_surname_with_two_candidates_is_left_alone(self):
        """Fletcher could be either, so it is not guessed at."""
        assert len(_groups([
            "Mark Fletcher", "Dale Fletcher", "Fletcher"])) == 3

    def test_an_initial_with_two_candidates_is_left_alone(self):
        assert len(_groups([
            "Mark Fletcher", "Marcus Fletcher", "M. Fletcher"])) == 3


class TestKeys:
    def test_a_resolved_key_is_shared_by_every_spelling(self):
        names = ["Mark Fletcher", "M. Fletcher", "Fletcher"]
        roster = resolve_roster(names)
        keys = {resolved_key(name, roster) for name in names}
        assert len(keys) == 1

    def test_an_empty_name_has_no_key(self):
        assert resolved_key("", {}) == ""
        assert resolved_key("   ", {}) == ""

    def test_a_name_the_roster_never_saw_keeps_its_own_key(self):
        assert resolved_key("Stranger", {}) == canonical_tag_key("Stranger")

    def test_an_empty_roster_resolves_to_nothing(self):
        assert resolve_roster([]) == {}
        assert resolve_roster(["", "  ", None]) == {}
