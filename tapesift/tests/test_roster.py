"""Jersey numbers, because that is what you can see on film."""

from __future__ import annotations

import pytest

from tapesift.services.roster_service import (
    Roster, RosterPlayer, bundled_rosters, load_bundled, load_for_project)


@pytest.fixture(scope="module")
def miami() -> Roster:
    return load_bundled("miami_2026")


class TestBundled:
    def test_the_squad_ships_with_the_app(self):
        assert "miami_2026" in bundled_rosters()
        assert "miami_2025" not in bundled_rosters()

    def test_it_is_a_whole_roster_not_a_sample(self, miami):
        assert len(miami) == 116

    def test_an_old_saved_slug_loads_the_current_roster(self, miami):
        assert load_bundled("miami_2025").players == miami.players

    def test_a_known_player_is_there_as_written_on_the_roster(self, miami):
        """Not "Chamar" and not "Elijah" - the sheet says otherwise."""
        names = miami.names()
        assert "CharMar Brown" in names
        assert "Elija Lofton" in names
        assert "Malachi Toney" in names
        assert "Mark Fletcher Jr." in names


class TestNumbers:
    @pytest.mark.parametrize("number,name", [
        ("1", "Malachi Toney"),
        ("2", "Jordan Lyle"),
        ("4", "Mark Fletcher Jr."),
        ("6", "CharMar Brown"),
        ("10", "Darian Mensah"),
    ])
    def test_current_official_jerseys(self, miami, number, name):
        assert name in {player.name for player in miami.by_number(number)}

    def test_a_number_finds_everyone_wearing_it(self, miami):
        assert {p.name for p in miami.by_number("4")} == {
            "Cam Pruitt", "Mark Fletcher Jr."}

    def test_a_shared_number_is_never_guessed(self, miami):
        """Two men wear 4. Picking one puts the wrong man on the play."""
        assert miami.resolve("4") == ""

    def test_a_number_only_one_man_wears_resolves(self, miami):
        assert miami.resolve("99") == "Ahmad Moten Sr."

    def test_a_hash_is_allowed(self, miami):
        assert miami.by_number("#99") == miami.by_number("99")

    @pytest.mark.parametrize("text", ["", "  ", "abc", "101", "4a"])
    def test_nonsense_finds_nobody(self, miami, text):
        assert miami.by_number(text) == []
        assert miami.resolve(text) == ""

    def test_the_label_reads_like_a_roster_line(self, miami):
        labels = [p.label for p in miami.by_number("10")]
        assert "#10 Darian Mensah (QB)" in labels


class TestEntry:
    def test_the_number_comes_back_off_before_saving(self, miami):
        assert miami.normalize_entry(
            "4 Mark Fletcher Jr.") == "Mark Fletcher Jr."

    def test_a_bare_unambiguous_number_becomes_the_name(self, miami):
        assert miami.normalize_entry("99") == "Ahmad Moten Sr."

    def test_a_bare_shared_number_is_left_as_typed(self, miami):
        assert miami.normalize_entry("4") == "4"

    def test_a_typed_name_is_left_alone(self, miami):
        assert miami.normalize_entry("Someone Else") == "Someone Else"

    def test_empty_stays_empty(self, miami):
        assert miami.normalize_entry("") == ""


class TestFiles:
    def test_a_roster_survives_a_round_trip(self, tmp_path):
        roster = Roster([
            RosterPlayer("4", "Mark Fletcher Jr.", "RB", "Junior"),
            RosterPlayer("11", "Carson Beck", "QB", "Senior")])
        path = roster.save(tmp_path / "roster.csv")
        again = Roster.load(path)
        assert again.names() == roster.names()
        assert again.by_number("4")[0].position == "RB"

    def test_a_missing_file_is_an_empty_roster_not_a_crash(self, tmp_path):
        assert len(Roster.load(tmp_path / "nope.csv")) == 0

    def test_a_project_roster_remains_its_own_roster(self, tmp_path):
        Roster([RosterPlayer("10", "Saved Project Player")]).save(
            tmp_path / "roster.csv")
        assert load_for_project(tmp_path).names() == ["Saved Project Player"]

    def test_rows_without_a_name_are_skipped(self):
        roster = Roster.from_rows([
            {"number": "4", "name": ""}, {"number": "5", "name": "Real"}])
        assert roster.names() == ["Real"]


class TestPickerEntry:
    """The number is how you find a player, not part of his name."""

    OFFERED = ("4 Mark Fletcher Jr.", "11 Carson Beck", "99 Ahmad Moten Sr.")

    @pytest.mark.parametrize("typed,stored", [
        ("4 Mark Fletcher Jr.", "Mark Fletcher Jr."),
        ("11 Carson Beck", "Carson Beck"),
        ("Malachi Toney", "Malachi Toney"),
        ("", ""),
    ])
    def test_the_jersey_number_comes_off_before_storing(self, typed, stored):
        from tapesift.services.roster_service import strip_jersey_number
        assert strip_jersey_number(typed, self.OFFERED) == stored

    def test_a_label_the_picker_never_offered_is_left_alone(self):
        """"#12 QB" is a whole label, not a number and a name.

        An opponent's names are often unknown, so a play gets logged by
        number and position. Stripping that leaves "QB", which is nobody.
        """
        from tapesift.services.roster_service import strip_jersey_number
        for label in ("#12 QB", "12 QB", "7 WR", "Mark 4 Fletcher"):
            assert strip_jersey_number(label, self.OFFERED) == label

    def test_with_no_roster_nothing_is_stripped(self):
        from tapesift.services.roster_service import strip_jersey_number
        assert strip_jersey_number("4 Mark Fletcher Jr.") == (
            "4 Mark Fletcher Jr.")
