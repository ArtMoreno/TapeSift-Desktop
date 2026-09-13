"""Details-prompt timing: the form must appear after the play, not before."""

from pathlib import Path

from tapesift.core.config import (
    AppSettings, DETAILS_AFTER_OUT, DETAILS_AT_IN, DETAILS_OFF,
)


class TestDetailsPromptSetting:
    def test_default_is_after_the_play(self):
        # You can't log a result you haven't seen yet.
        assert AppSettings().details_prompt == DETAILS_AFTER_OUT

    def test_round_trip(self, tmp_path: Path):
        settings = AppSettings()
        settings.details_prompt = DETAILS_AT_IN
        target = tmp_path / "settings.json"
        settings.save(target)
        assert AppSettings.load(target).details_prompt == DETAILS_AT_IN

    def test_migrates_old_guided_flag(self, tmp_path: Path):
        target = tmp_path / "settings.json"
        target.write_text('{"i_key_guided": true, "volume": 55}',
                          encoding="utf-8")
        loaded = AppSettings.load(target)
        assert loaded.details_prompt == DETAILS_AFTER_OUT  # improved default
        assert loaded.volume == 55

    def test_migrates_old_quick_mode_to_off(self, tmp_path: Path):
        target = tmp_path / "settings.json"
        target.write_text('{"i_key_guided": false}', encoding="utf-8")
        assert AppSettings.load(target).details_prompt == DETAILS_OFF

    def test_explicit_value_wins_over_legacy_flag(self, tmp_path: Path):
        target = tmp_path / "settings.json"
        target.write_text(
            '{"i_key_guided": false, "details_prompt": "at_in"}',
            encoding="utf-8")
        assert AppSettings.load(target).details_prompt == DETAILS_AT_IN
