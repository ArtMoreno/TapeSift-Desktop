"""Tag canonicalisation - real duplicate variants seen in the user's library."""

import pytest

from tapesift.models.clip import Clip
from tapesift.services import library_service, tag_service


class TestTagKey:
    @pytest.mark.parametrize("a,b", [
        ("Mark Fletcher Jr.", "Mark Fletcher jr"),
        ("CJ Daniels", "Cj Daniels"),
        ("cj daniels", "CJ  Daniels"),
        ("Run", "run"),
        ("  Pass TD ", "pass td"),
    ])
    def test_variants_share_a_key(self, a, b):
        assert tag_service.tag_key(a) == tag_service.tag_key(b)

    def test_distinct_tags_stay_distinct(self):
        assert tag_service.tag_key("Run") != tag_service.tag_key("Rush")
        assert tag_service.tag_key("Pass TD") != tag_service.tag_key("Rushing TD")

    def test_blank(self):
        assert tag_service.tag_key("   ") == ""


class TestCanonicalDisplay:
    def test_most_common_variant_wins(self):
        assert tag_service.canonical_display(
            ["run", "Run", "Run", "run"]) == "Run"

    def test_ties_prefer_proper_casing(self):
        assert tag_service.canonical_display(["cj daniels", "CJ Daniels"]) == \
            "CJ Daniels"

    def test_dedupe_keeps_first_spelling_and_order(self):
        assert tag_service.dedupe_tags(
            ["Run", "run", "Pass TD", "RUN"]) == ["Run", "Pass TD"]

    def test_player_display_prefers_readable_casing_over_lowercase_frequency(self):
        merged = tag_service.merge_player_variants([
            "Jordan Davis", "jordan davis", "jordan davis", "JORDAN DAVIS",
        ])
        assert merged["jordan davis"] == "Jordan Davis"


class TestDisplayTitle:
    def test_camel_case_split(self):
        assert tag_service.clean_display_title("MarkFletcherJRBigRun") == \
            "Mark Fletcher JR Big Run"

    def test_separators_become_spaces(self):
        assert tag_service.clean_display_title("001_Opening-sequence") == \
            "001 Opening sequence"

    def test_saved_title_wins(self):
        assert tag_service.display_title("Big run", filename_base="X_y") == "Big run"

    def test_falls_back_through_the_chain(self):
        assert tag_service.display_title("", "3rd Down", "Pressure") == \
            "3rd Down · Pressure"
        assert tag_service.display_title("", "", "", "MalachiToneyRC1") == \
            "Malachi Toney RC1"
        assert tag_service.display_title("", "", "", "") == "Untitled Clip"


class TestLibraryUsesNormalisedTags:
    @pytest.fixture
    def catalog(self, tmp_path, monkeypatch):
        db = tmp_path / "library.db"
        monkeypatch.setattr(library_service, "catalog_path", lambda: db)
        return db

    def _index(self, project, clips):
        rows = library_service.build_index_payload(
            project, "Game", "C:/v.mp4", clips)
        library_service.write_project_index(project, rows)

    def test_search_finds_all_casings(self, catalog):
        self._index("C:/p/a.clipforge", [
            Clip(start_ms=0, end_ms=1000, clip_title="A", tags=["Mark Fletcher Jr."]),
            Clip(start_ms=0, end_ms=1000, clip_title="B", tags=["Mark Fletcher jr"]),
            Clip(start_ms=0, end_ms=1000, clip_title="C", tags=["mark fletcher jr"]),
        ])
        # Any spelling of the filter finds all three clips.
        for spelling in ("Mark Fletcher Jr.", "mark fletcher jr", "MARK FLETCHER JR"):
            assert len(library_service.search(tags=[spelling])) == 3, spelling

    def test_tag_list_shows_one_entry_per_tag(self, catalog):
        self._index("C:/p/a.clipforge", [
            Clip(start_ms=0, end_ms=1000, clip_title="A", tags=["Run", "run"]),
            Clip(start_ms=0, end_ms=1000, clip_title="B", tags=["RUN"]),
        ])
        tags = library_service.all_tags()
        assert tags == ["Run"], tags       # one chip, not three

    def test_clip_tags_are_never_rewritten(self, catalog):
        clip = Clip(start_ms=0, end_ms=1000, clip_title="A",
                    tags=["Mark Fletcher jr", "run"])
        self._index("C:/p/a.clipforge", [clip])
        # Indexing must not mutate what the user typed on the clip itself.
        assert clip.tags == ["Mark Fletcher jr", "run"]
