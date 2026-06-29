from utils.text.structured_response import (
    diagnose_structured_response,
    extract_json_response_text,
)


def test_extract_json_response_text_handles_plain_json():
    assert extract_json_response_text('{"answer": 1}') == '{"answer": 1}'


def test_extract_json_response_text_handles_fenced_json():
    assert extract_json_response_text('```json\n{"answer": 1}\n```') == '{"answer": 1}'


def test_extract_json_response_text_handles_embedded_json():
    assert (
        extract_json_response_text('before {"answer": {"nested": true}} after')
        == '{"answer": {"nested": true}}'
    )


def test_diagnose_structured_response_accepts_valid_schema():
    data, reason, raw = diagnose_structured_response(
        '{"parts": [{"type": "text", "content": "hello"}, {"type": "code", "language": "python", "content": "print(1)"}]}'
    )

    assert reason == "ok"
    assert raw
    assert data == {
        "parts": [
            {"type": "text", "content": "hello"},
            {"type": "code", "language": "python", "content": "print(1)"},
        ]
    }


def test_diagnose_structured_response_rejects_invalid_schema():
    data, reason, raw = diagnose_structured_response(
        '{"parts": [{"type": "image", "content": "x"}]}'
    )

    assert data is None
    assert reason == "invalid_schema:part_type:image"
    assert raw
