import pytest

from tapesift.core.exceptions import InvalidRangeError, TimestampParseError
from tapesift.services.timestamp_parser import (
    clamp_range, format_ms, format_ms_filename, parse_range, parse_timestamp,
)


class TestParseTimestamp:
    @pytest.mark.parametrize("text,expected_ms", [
        ("03:14", 194_000),
        ("03:14.500", 194_500),
        ("01:03:14", 3_794_000),
        ("01:03:14.250", 3_794_250),
        ("194", 194_000),
        ("194.5", 194_500),
        ("0", 0),
        ("00:00", 0),
        ("0:05", 5_000),
        ("  03:14  ", 194_000),
        ("90:30", 5_430_000),  # MM:SS with minutes > 59 = total minutes
    ])
    def test_valid(self, text, expected_ms):
        assert parse_timestamp(text) == expected_ms

    @pytest.mark.parametrize("text", [
        "abc", "03:72", "-10", "01:03:14:20", "", "   ", "1:2:3:4", "10:-5", "::", None,
    ])
    def test_invalid(self, text):
        with pytest.raises(TimestampParseError):
            parse_timestamp(text)

    def test_error_message_is_readable(self):
        with pytest.raises(TimestampParseError) as exc:
            parse_timestamp("03:72")
        assert "03:72" in str(exc.value)


class TestParseRange:
    def test_valid_range(self):
        assert parse_range("03:09", "03:22") == (189_000, 202_000)

    def test_end_before_start(self):
        with pytest.raises(InvalidRangeError):
            parse_range("03:22", "03:09")

    def test_end_equal_start(self):
        with pytest.raises(InvalidRangeError):
            parse_range("03:09", "03:09")


class TestClampRange:
    def test_no_clamp(self):
        assert clamp_range(1000, 5000, 100_000) == (1000, 5000, False)

    def test_clamp_below_zero(self):
        start, end, clamped = clamp_range(-3000, 5000, 100_000)
        assert (start, end, clamped) == (0, 5000, True)

    def test_clamp_beyond_duration(self):
        start, end, clamped = clamp_range(90_000, 120_000, 100_000)
        assert (start, end, clamped) == (90_000, 100_000, True)

    def test_zero_duration_means_unknown(self):
        # Unknown duration: only the zero floor applies.
        assert clamp_range(5000, 999_999_999, 0) == (5000, 999_999_999, False)


class TestFormatting:
    def test_format_short(self):
        assert format_ms(194_000) == "03:14"

    def test_format_hours(self):
        assert format_ms(3_794_000) == "01:03:14"

    def test_format_millis(self):
        assert format_ms(194_500, show_millis=True) == "03:14.500"

    def test_filename_format_has_no_colons(self):
        assert format_ms_filename(3_794_000) == "01-03-14"

    def test_roundtrip(self):
        assert parse_timestamp(format_ms(3_794_000)) == 3_794_000
