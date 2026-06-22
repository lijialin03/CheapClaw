from utils.text_helpers import (
    clean_generated_file_content,
    diagnose_structured_response,
    extract_json_response_text,
    markdown_to_plain,
    restore_rendered_code_blocks,
    truncate_text_fields,
)


def test_markdown_to_plain_removes_syntax_and_preserves_code_content():
    markdown = """# Title

This is **bold** and [linked](https://example.com).

```python
print("hello")
```

| A | B |
| - | - |
| 1 | 2 |
"""

    plain = markdown_to_plain(markdown)

    assert "#" not in plain
    assert "**" not in plain
    assert "https://example.com" not in plain
    assert 'print("hello")' in plain
    assert "Title" in plain
    assert "linked" in plain


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


def test_restore_rendered_code_blocks_reconstructs_markdown_fence():
    rendered = "Here is code:\npython\n1\n2\nimport os\nprint(os.name)\nDone"

    restored = restore_rendered_code_blocks(rendered)

    assert restored == "Here is code:\n```python\nimport os\nprint(os.name)\n```\nDone"


def test_clean_generated_file_content_strips_fences_language_labels_and_line_numbers():
    content = """```python
1 import os
2 print(os.name)
```"""

    assert clean_generated_file_content(content) == "import os\n\nprint(os.name)"


def test_clean_generated_file_content_preserves_plain_yaml_structure():
    content = "repos:\n  - repo: x\n    hooks:\n      - id: check-yaml\n"

    assert (
        clean_generated_file_content(content)
        == "repos:\n  - repo: x\n    hooks:\n      - id: check-yaml"
    )


def test_clean_generated_file_content_extracts_fenced_yaml():
    content = """```yaml
repos:
  - repo: x
    hooks:
      - id: check-yaml
```"""

    assert (
        clean_generated_file_content(content)
        == "repos:\n  - repo: x\n    hooks:\n      - id: check-yaml"
    )


def test_clean_generated_file_content_restores_rendered_yaml_code_block():
    rendered = (
        "yaml\n1\n2\n3\n4\nrepos:\n  - repo: x\n    hooks:\n      - id: check-yaml"
    )

    assert (
        clean_generated_file_content(rendered)
        == "repos:\n  - repo: x\n    hooks:\n      - id: check-yaml"
    )


def test_truncate_text_fields_truncates_long_strings_and_records_removed_chars():
    data = {"short": "ok", "long": "abcdef", "other": 123}

    truncated = truncate_text_fields(data, ("short", "long", "other"), 3)

    assert truncated["short"] == "ok"
    assert truncated["long"] == "abc\n...[truncated]"
    assert truncated["truncated_long_chars"] == 3
    assert truncated["other"] == 123
    assert "truncated_short_chars" not in truncated
