import pytest

from tapesift.core.exceptions import CsvImportError
from tapesift.services.csv_import_service import detect_mapping, parse_csv_text


class TestDetectMapping:
    def test_exact_names(self):
        m = detect_mapping(["timestamp", "clip_name", "label", "tags", "notes"])
        assert m["timestamp"] == "timestamp"
        assert m["clip_name"] == "clip_name"

    def test_aliases(self):
        m = detect_mapping(["Start Time", "End Time", "Title"])
        assert m["start"] == "Start Time"
        assert m["end"] == "End Time"
        assert m["title"] == "Title"


class TestParseCsv:
    def test_timestamp_and_name(self):
        result = parse_csv_text("timestamp,clip_name\n03:14,Opening sequence\n18:45,Reaction\n")
        assert len(result.rows) == 2
        assert result.rows[0].timestamp_ms == 194_000
        assert result.rows[0].name == "Opening sequence"

    def test_range_rows(self):
        result = parse_csv_text("start,end,title\n03:09,03:22,Opening\n")
        row = result.rows[0]
        assert (row.start_ms, row.end_ms, row.name) == (189_000, 202_000, "Opening")

    def test_naming_priority_clip_name_over_title(self):
        result = parse_csv_text(
            "timestamp,clip_name,title,filename\n03:14,Winner,Loser,also-loser\n")
        assert result.rows[0].name == "Winner"

    def test_naming_priority_title_over_filename(self):
        result = parse_csv_text("timestamp,title,filename\n03:14,Winner,loser\n")
        assert result.rows[0].name == "Winner"

    def test_bad_rows_collected_good_rows_kept(self):
        result = parse_csv_text("timestamp,clip_name\nabc,Bad\n03:14,Good\n")
        assert len(result.rows) == 1
        assert result.rows[0].name == "Good"
        assert len(result.errors) == 1
        assert result.errors[0][0] == 2  # line number

    def test_tags_split(self):
        result = parse_csv_text("timestamp,clip_name,tags\n03:14,X,\"a, b;c\"\n")
        assert result.rows[0].tags == ["a", "b", "c"]

    def test_no_time_column_raises(self):
        with pytest.raises(CsvImportError):
            parse_csv_text("clip_name,label\nX,Y\n")

    def test_range_end_before_start_is_error(self):
        result = parse_csv_text("start,end,title\n03:22,03:09,Backwards\n")
        assert not result.rows
        assert len(result.errors) == 1
