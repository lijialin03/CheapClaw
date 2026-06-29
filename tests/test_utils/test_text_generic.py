from utils.text.generic import truncate_text_fields


def test_truncate_text_fields_truncates_long_strings_and_records_removed_chars():
    data = {"short": "ok", "long": "abcdef", "other": 123}

    truncated = truncate_text_fields(data, ("short", "long", "other"), 3)

    assert truncated["short"] == "ok"
    assert truncated["long"] == "abc\n...[truncated]"
    assert truncated["truncated_long_chars"] == 3
    assert truncated["other"] == 123
    assert "truncated_short_chars" not in truncated
