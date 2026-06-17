# utils/text_helpers.py
import json
import re


def markdown_to_plain(text: str) -> str:
    """
    将 Markdown 文本转换为纯文本，移除所有格式标记（代码块、加粗、链接、表格等）。
    """
    text = remove_line_numbers_keep_markdown(text)
    text = re.sub(r"```(?:\w+)?\n(.*?)\n```", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^\)]+\)", "", text)
    text = re.sub(r"^\|[\s\-:]+\|$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\|", " ", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return text.strip()


def get_code(text: str) -> str | None:
    code_blocks = re.findall(r"```(?:\w+)?\n(.*?)```", text, re.DOTALL)
    if code_blocks:
        return code_blocks[0]
    return None


def parse_structured_response(text: str) -> dict | None:
    raw = extract_json_response_text(text)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    parts = data.get("parts") if isinstance(data, dict) else None
    if not isinstance(parts, list) or not parts:
        return None

    normalized_parts = []
    for part in parts:
        if not isinstance(part, dict):
            return None
        part_type = part.get("type")
        if part_type not in {"text", "code"}:
            return None
        content = part.get("content")
        if not isinstance(content, str):
            return None
        normalized_parts.append(part)
    return {"parts": normalized_parts}


def extract_json_response_text(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{"):
        return stripped
    match = re.fullmatch(r"(?s)```(?:json)?\n(.*?)\n```", stripped)
    if match:
        return match.group(1).strip()
    return ""


def remove_line_numbers_keep_markdown(text: str) -> str:
    """
    仅移除每行开头的行号（如 "1 ", "2."），不删除代码块标记或其他 Markdown 语法。
    """
    return re.sub(r"^([ \t]*)\d{1,3}[ \t.、]+", r"\1", text, flags=re.MULTILINE)


def restore_rendered_code_blocks(text: str) -> str:
    """将网页 inner_text 中的“语言名 + 行号列 + 代码”还原为 Markdown 代码块。"""
    lines = text.splitlines()
    restored: list[str] = []
    index = 0
    languages = {"python", "py", "javascript", "typescript", "json", "markdown", "html", "css", "bash", "shell", "sh"}

    while index < len(lines):
        language = lines[index].strip().lower()
        if language not in languages:
            restored.append(lines[index])
            index += 1
            continue

        number_start = index + 1
        number_end = number_start
        while number_end < len(lines) and lines[number_end].strip().isdigit():
            number_end += 1

        line_count = number_end - number_start
        code_end = number_end + line_count
        if line_count == 0 or code_end > len(lines):
            restored.append(lines[index])
            index += 1
            continue

        code_lines = lines[number_end:code_end]
        if not _looks_like_rendered_code(language, code_lines):
            restored.append(lines[index])
            index += 1
            continue

        restored.append(f"```{language}")
        restored.extend(code_lines)
        restored.append("```")
        index = code_end

    return "\n".join(restored)


def _looks_like_rendered_code(language: str, code_lines: list[str]) -> bool:
    code = "\n".join(code_lines)
    if language in {"python", "py"}:
        return _looks_like_python_code(code) or any(token in code for token in ("=", "with ", "open(", "b\"", "print("))
    return bool(re.search(r"[{}();=<>]|^\s*(const|let|var|function|import|export|class|def|if|for|while)\b", code, re.MULTILINE))


def strip_code_fence(text: str, preserve_inner: bool = False) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        content = "\n".join(lines[1:-1])
        return content if preserve_inner else content.strip()
    return text


def clean_generated_file_content(text: str) -> str:
    code = get_code(text)
    if code is not None:
        text = code
    text = remove_line_numbers_keep_markdown(text.replace("\u00a0", " "))
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].strip().lower() in {"python", "py", "javascript", "typescript", "json", "markdown", "html", "css"}:
        lines.pop(0)
    cleaned = "\n".join(line for line in lines if not line.strip().isdigit())
    if _looks_like_python_code(cleaned):
        return _restore_python_spacing(cleaned)
    return cleaned


def _looks_like_python_code(text: str) -> bool:
    return bool(re.search(r"(?m)^(from\s+\S+\s+import\s+|import\s+\S+|class\s+\w+|def\s+\w+)", text))


def _restore_python_spacing(text: str) -> str:
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
        is_import = indent == 0 and (stripped.startswith("import ") or stripped.startswith("from "))
        is_top_level_def = indent == 0 and (stripped.startswith("class ") or stripped.startswith("def "))
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


def first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def truncate_text_fields(data: dict, fields: tuple[str, ...], max_chars: int) -> dict:
    compact = dict(data)
    for field in fields:
        value = compact.get(field)
        if isinstance(value, str) and len(value) > max_chars:
            compact[field] = value[:max_chars] + "\n...[truncated]"
            compact[f"truncated_{field}_chars"] = len(value) - max_chars
    return compact
