from pathlib import Path

from tapesift.models.clip import Clip
from tapesift.services import filename_service
from tapesift.services.filename_service import unique_title
from tapesift.services.filename_service import (
    effective_base, fallback_name, find_duplicate_bases, render_template,
    sanitize_filename_base, unique_path,
)


def make_clip(**kwargs) -> Clip:
    defaults = dict(start_ms=1_120_000, end_ms=1_135_000)
    defaults.update(kwargs)
    return Clip(**defaults)


class TestSanitize:
    def test_spec_example(self):
        assert sanitize_filename_base("Final reveal: close up / version 2?") == \
            "Final-reveal-close-up-version-2"

    def test_simple_name(self):
        assert sanitize_filename_base("Final reveal close up") == "Final-reveal-close-up"

    def test_underscore_style(self):
        assert sanitize_filename_base("Final reveal", "underscore") == "Final_reveal"

    def test_all_invalid_chars_removed(self):
        result = sanitize_filename_base('a<b>c:d"e/f\\g|h?i*j')
        for ch in '<>:"/\\|?*':
            assert ch not in result

    def test_collapse_spaces(self):
        assert sanitize_filename_base("a    b     c") == "a-b-c"

    def test_trims(self):
        assert sanitize_filename_base("   hello   ") == "hello"

    def test_empty_returns_empty(self):
        assert sanitize_filename_base("") == ""
        assert sanitize_filename_base("???") == ""

    def test_reserved_name(self):
        assert sanitize_filename_base("CON") != "CON"

    def test_unicode_preserved(self):
        assert sanitize_filename_base("café interview") == "café-interview"

    def test_length_capped(self):
        assert len(sanitize_filename_base("x" * 500)) <= 180


class TestNamingPriority:
    def test_explicit_base_wins(self):
        clip = make_clip(clip_title="Title Here", output_filename_base="my custom name")
        assert effective_base(clip) == "my-custom-name"

    def test_title_used_when_no_base(self):
        clip = make_clip(clip_title="Final reveal close up")
        assert effective_base(clip) == "Final-reveal-close-up"

    def test_fallback_when_nothing(self):
        clip = make_clip(clip_number=1)
        assert effective_base(clip) == fallback_name(clip)
        assert effective_base(clip)  # never blank

    def test_fallback_with_central_timestamp(self):
        clip = make_clip(clip_number=1, central_timestamp_ms=1_125_000)  # 18:45
        assert fallback_name(clip) == "Clip_001_00-18-45"

    def test_fallback_range_form(self):
        clip = make_clip(clip_number=1, start_ms=1_120_000, end_ms=1_135_000)
        assert fallback_name(clip) == "Clip_001_00-18-40_to_00-18-55"


class TestTemplate:
    def test_default_template(self):
        clip = make_clip(clip_number=3, clip_title="Final reveal close up")
        assert render_template("{clip_number}_{clip_name}", clip) == \
            "003_Final-reveal-close-up"

    def test_project_variable(self):
        clip = make_clip(clip_number=1, clip_title="Intro")
        result = render_template("{project}_{clip_name}", clip, project_name="My Show")
        assert result == "My-Show_Intro"

    def test_unknown_variable_dropped(self):
        clip = make_clip(clip_number=1, clip_title="Intro")
        assert "nonsense" not in render_template("{nonsense}_{clip_name}", clip)

    def test_never_blank(self):
        clip = make_clip(clip_number=7)
        assert render_template("{label}", clip)  # label empty → fallback


class TestFolderTemplate:
    def clip(self) -> Clip:
        return Clip(start_ms=0, end_ms=1000, clip_title="Play",
                    tags=["Pressure", "Explosive"], label="Defense",
                    details={"player_name": "Damon Wilson", "quarter": "Q3",
                             "down_distance": "3rd & 7"})

    def test_basic_template(self):
        rel = filename_service.render_folder_template(
            "{player}/{quarter}/{down_distance}", self.clip())
        assert rel == "Damon-Wilson/Q3/3rd-&-7"

    def test_missing_value_becomes_unspecified(self):
        rel = filename_service.render_folder_template(
            "{player}/{result}", self.clip())
        assert rel == "Damon-Wilson/Unspecified"

    def test_tag_and_label_tokens(self):
        rel = filename_service.render_folder_template(
            "{tag}/{label}", self.clip())
        assert rel == "Pressure/Defense"

    def test_literal_text_mixed_with_tokens(self):
        rel = filename_service.render_folder_template(
            "Game Film/{quarter}", self.clip())
        assert rel == "Game-Film/Q3"

    def test_backslashes_and_empty_levels(self):
        rel = filename_service.render_folder_template(
            "\\{player}\\\\{quarter}\\", self.clip())
        assert rel == "Damon-Wilson/Q3"

    def test_invalid_characters_sanitized_per_level(self):
        clip = self.clip()
        clip.details["player_name"] = 'Wil:son * "Jr"'
        rel = filename_service.render_folder_template("{player}", clip)
        assert ":" not in rel and "*" not in rel and '"' not in rel

    def test_empty_template_returns_empty(self):
        assert filename_service.render_folder_template("", self.clip()) == ""


class TestUniquePath:
    def test_no_collision(self, tmp_path: Path):
        assert unique_path(tmp_path, "Final-reveal", ".mp4").name == "Final-reveal.mp4"

    def test_disk_collision(self, tmp_path: Path):
        (tmp_path / "Final-reveal.mp4").touch()
        assert unique_path(tmp_path, "Final-reveal", ".mp4").name == "Final-reveal_2.mp4"

    def test_batch_collisions(self, tmp_path: Path):
        taken: set[str] = set()
        names = [unique_path(tmp_path, "Final-reveal", ".mp4", taken).name for _ in range(3)]
        assert names == ["Final-reveal.mp4", "Final-reveal_2.mp4", "Final-reveal_3.mp4"]

    def test_case_insensitive_batch(self, tmp_path: Path):
        taken: set[str] = set()
        unique_path(tmp_path, "Clip", ".mp4", taken)
        assert unique_path(tmp_path, "clip", ".mp4", taken).name == "clip_2.mp4"


class TestDuplicateDetection:
    def test_finds_duplicates(self):
        a = make_clip(clip_number=1, clip_title="Same")
        b = make_clip(clip_number=1, clip_title="Same")
        dupes = find_duplicate_bases([a, b], "{clip_name}")
        assert dupes == {"same"}

    def test_disabled_clips_ignored(self):
        a = make_clip(clip_number=1, clip_title="Same")
        b = make_clip(clip_number=1, clip_title="Same", enabled=False)
        assert find_duplicate_bases([a, b], "{clip_name}") == set()


class TestUniqueTitle:
    def test_untouched_when_new(self):
        assert unique_title("Screen left", ["Other"]) == "Screen left"

    def test_numbers_a_repeat(self):
        assert unique_title("Screen left", ["Screen left"]) == "Screen left 2"

    def test_case_insensitive_and_continues_numbering(self):
        existing = ["screen left", "Screen Left 2", "SCREEN LEFT 3"]
        assert unique_title("Screen Left", existing) == "Screen Left 4"

    def test_blank_passes_through(self):
        assert unique_title("   ", ["x"]) == ""
