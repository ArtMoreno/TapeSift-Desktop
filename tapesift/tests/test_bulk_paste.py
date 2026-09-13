from tapesift.services.bulk_paste_parser import parse_bulk_text


class TestBulkPaste:
    def test_spec_single_format(self):
        text = "03:14 | Opening sequence\n18:45 | Important reaction shot\n42:11 | Final reveal\n"
        result = parse_bulk_text(text)
        assert len(result.rows) == 3
        assert not result.errors
        first = result.rows[0]
        assert first.timestamp_ms == 194_000
        assert first.name == "Opening sequence"

    def test_spec_range_format(self):
        text = "03:09 - 03:22 | Opening sequence\n18:40 - 18:55 | Important reaction shot\n"
        result = parse_bulk_text(text)
        assert len(result.rows) == 2
        assert result.rows[0].start_ms == 189_000
        assert result.rows[0].end_ms == 202_000
        assert result.rows[0].name == "Opening sequence"

    def test_blank_lines_ignored(self):
        result = parse_bulk_text("\n03:14 | A\n\n\n18:45 | B\n\n")
        assert len(result.rows) == 2

    def test_whitespace_trimmed(self):
        result = parse_bulk_text("   03:14   |   Padded name   ")
        assert result.rows[0].name == "Padded name"

    def test_bad_rows_reported_good_rows_kept(self):
        result = parse_bulk_text("03:14 | Good\ngarbage line here ok\n18:45 | Also good")
        assert len(result.rows) == 2
        assert len(result.errors) == 1
        line_number, raw, message = result.errors[0]
        assert line_number == 2
        assert "garbage" in raw
        assert message  # readable message present

    def test_timestamp_only(self):
        result = parse_bulk_text("03:14")
        assert result.rows[0].timestamp_ms == 194_000
        assert result.rows[0].name == ""

    def test_space_separated_name(self):
        result = parse_bulk_text("03:14 Opening sequence")
        assert result.rows[0].name == "Opening sequence"

    def test_range_end_before_start(self):
        result = parse_bulk_text("05:00 - 04:00 | Backwards")
        assert not result.rows
        assert len(result.errors) == 1
