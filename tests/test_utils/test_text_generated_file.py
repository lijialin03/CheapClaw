from cheapclaw.utils.text.generated_file import clean_generated_file_content


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


def test_clean_generated_file_content_strips_toolbar_buttons_from_language_label():
    rendered = "htmlCopyDownloadRun\n1\n2\n<!DOCTYPE html>\n<html>"
    assert clean_generated_file_content(rendered) == "<!DOCTYPE html>\n<html>"


def test_clean_generated_file_content_strips_toolbar_buttons_from_css_block():
    rendered = "cssCopyDownload\n1\n2\n3\nbody {\n  color: red;\n}"
    assert clean_generated_file_content(rendered) == "body {\n  color: red;\n}"


def test_clean_generated_file_content_strips_inline_toolbar_prefix_from_html_block():
    rendered = "htmlCopyDownloadRun<!DOCTYPE html>\n<html>"
    assert clean_generated_file_content(rendered) == "<!DOCTYPE html>\n<html>"


def test_clean_generated_file_content_keeps_plain_text_that_starts_like_toolbar_prefix():
    rendered = "htmlcopySomething meaningful\nnext line"
    assert clean_generated_file_content(rendered) == rendered
