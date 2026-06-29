from utils.text.markdown import convert_markdown_to_plain_text


def test_convert_markdown_to_plain_text_removes_syntax_and_preserves_code_content():
    markdown = """# Title

This is **bold** and [linked](https://example.com).

```python
print("hello")
```

| A | B |
| - | - |
| 1 | 2 |
"""

    plain = convert_markdown_to_plain_text(markdown)

    assert "#" not in plain
    assert "**" not in plain
    assert "https://example.com" not in plain
    assert 'print("hello")' in plain
    assert "Title" in plain
    assert "linked" in plain
