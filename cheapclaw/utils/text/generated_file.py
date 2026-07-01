from .markdown import extract_first_fenced_code_block, strip_leading_line_numbers
from .rendered_code_blocks import (
    RENDERED_CODE_LANGUAGE_LABELS,
    looks_like_python_code,
    normalize_rendered_language_label,
    restore_rendered_code_blocks,
    strip_inline_toolbar_prefix,
)


def clean_generated_file_content(text: str) -> str:
    """清洗模型生成内容，使其适合直接写入文件。"""
    text = _normalize_rendered_code_text(text)
    text = _extract_fenced_code_if_present(text)
    text = strip_leading_line_numbers(text)

    lines = _strip_leading_empty_lines(text.splitlines())
    lines = _strip_leading_rendered_code_label(lines)
    lines = _strip_leading_empty_lines(lines)

    cleaned = _drop_standalone_line_numbers(lines)
    if looks_like_python_code(cleaned):
        return _restore_python_spacing(cleaned)
    return cleaned


def _normalize_rendered_code_text(text: str) -> str:
    """处理网页 innerText 中的非标准空格，并还原 rendered code block。"""
    return restore_rendered_code_blocks(text.replace("\u00a0", " "))


def _extract_fenced_code_if_present(text: str) -> str:
    code = extract_first_fenced_code_block(text)
    return code if code is not None else text


def _strip_leading_empty_lines(lines: list[str]) -> list[str]:
    first_content_line = 0
    while first_content_line < len(lines) and not lines[first_content_line].strip():
        first_content_line += 1
    return lines[first_content_line:]


def _strip_leading_rendered_code_label(lines: list[str]) -> list[str]:
    """清理文件开头由网页代码块语言标签和 toolbar 文本造成的污染。"""
    if not lines:
        return lines

    if (
        normalize_rendered_language_label(lines[0].strip())
        in RENDERED_CODE_LANGUAGE_LABELS
    ):
        return lines[1:]

    lines[0] = strip_inline_toolbar_prefix(lines[0])
    return lines


def _drop_standalone_line_numbers(lines: list[str]) -> str:
    return "\n".join(line for line in lines if not line.strip().isdigit())


def _restore_python_spacing(text: str) -> str:
    """轻量恢复 Python 代码的 import、顶层定义和方法定义间距。"""
    result: list[str] = []
    previous_was_import = False
    previous_indent = 0

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            _ensure_trailing_blank_lines(result, 1)
            previous_was_import = False
            continue

        indent = len(line) - len(line.lstrip(" "))
        is_import = indent == 0 and (
            stripped.startswith("import ") or stripped.startswith("from ")
        )
        is_top_level_def = indent == 0 and (
            stripped.startswith("class ") or stripped.startswith("def ")
        )
        is_method_def = indent > 0 and stripped.startswith("def ")

        if is_top_level_def:
            _ensure_trailing_blank_lines(result, 2)
        elif is_method_def:
            _ensure_trailing_blank_lines(result, 1)
        elif previous_was_import and not is_import:
            _ensure_trailing_blank_lines(result, 1)
        elif indent == 0 and previous_indent > 0:
            _ensure_trailing_blank_lines(result, 2)

        result.append(line.rstrip())
        previous_was_import = is_import
        previous_indent = indent

    return "\n".join(result).rstrip()


def _ensure_trailing_blank_lines(lines: list[str], count: int) -> None:
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        lines.extend([""] * count)
