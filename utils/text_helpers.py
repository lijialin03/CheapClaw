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
    data, _reason, _raw = diagnose_structured_response(text)
    return data


def diagnose_structured_response(text: str) -> tuple[dict | None, str, str]:
    raw = extract_json_response_text(text)
    if not raw:
        return None, "no_json_candidate", ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        repaired = _escape_control_chars_in_json_strings(raw)
        if repaired != raw:
            try:
                data = json.loads(repaired)
                raw = repaired
            except json.JSONDecodeError:
                data = _parse_rendered_structured_response(raw)
                if data is None:
                    return None, f"json_decode_error:{exc.msg}:line={exc.lineno}:col={exc.colno}", raw
        else:
            data = _parse_rendered_structured_response(raw)
            if data is None:
                return None, f"json_decode_error:{exc.msg}:line={exc.lineno}:col={exc.colno}", raw
    parts = data.get("parts") if isinstance(data, dict) else None
    if not isinstance(parts, list) or not parts:
        return None, "invalid_schema:parts", raw

    normalized_parts = []
    for part in parts:
        if not isinstance(part, dict):
            return None, "invalid_schema:part_not_object", raw
        part_type = part.get("type")
        if part_type not in {"text", "code"}:
            return None, f"invalid_schema:part_type:{part_type}", raw
        content = part.get("content")
        if not isinstance(content, str):
            return None, "invalid_schema:content_not_string", raw
        normalized_part = {"type": part_type, "content": content}
        if part_type == "code" and isinstance(part.get("language"), str):
            normalized_part["language"] = part["language"]
        normalized_parts.append(normalized_part)
    return {"parts": normalized_parts}, "ok", raw


def _parse_rendered_structured_response(text: str) -> dict | None:
    if '"parts"' not in text:
        return None
    parts: list[dict] = []
    for match in re.finditer(r'"type"\s*:\s*"(text|code)"', text):
        part_type = match.group(1)
        content_match = re.search(r'"content"\s*:\s*"', text[match.end():])
        if not content_match:
            return None
        content_start = match.end() + content_match.end()
        next_part = re.search(r'\n\s*}\s*,\s*\n\s*\{\s*\n\s*"type"\s*:', text[content_start:])
        final_part = re.search(r'\n\s*}\s*\n\s*]\s*\n\s*}\s*$', text[content_start:])
        if next_part:
            content_end = content_start + next_part.start()
        elif final_part:
            content_end = content_start + final_part.start()
        else:
            return None
        content = _decode_rendered_json_string_content(text[content_start:content_end])
        part = {"type": part_type, "content": content}
        if part_type == "code":
            language_match = re.search(r'"language"\s*:\s*"([^"\n\r]*)"', text[match.end():content_start])
            if language_match:
                part["language"] = language_match.group(1)
        parts.append(part)
    return {"parts": parts} if parts else None


def _decode_rendered_json_string_content(text: str) -> str:
    content = text
    if content.endswith('"'):
        content = content[:-1]
    try:
        return json.loads(f'"{_escape_control_chars_in_json_strings(content)}"')
    except json.JSONDecodeError:
        return content.replace('\\n', '\n').replace('\\r', '\r').replace('\\t', '\t')


def _escape_control_chars_in_json_strings(text: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    changed = False
    for char in text:
        if in_string:
            if escaped:
                result.append(char)
                escaped = False
            elif char == "\\":
                result.append(char)
                escaped = True
            elif char == '"':
                result.append(char)
                in_string = False
            elif char == "\n":
                result.append("\\n")
                changed = True
            elif char == "\r":
                result.append("\\r")
                changed = True
            elif char == "\t":
                result.append("\\t")
                changed = True
            else:
                result.append(char)
            continue

        result.append(char)
        if char == '"':
            in_string = True
    return "".join(result) if changed else text


def extract_json_response_text(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{"):
        return stripped
    match = re.fullmatch(r"(?is)```\s*(?:json)?\s*\n(.*?)\n```", stripped)
    if match:
        return match.group(1).strip()
    return _extract_first_json_object(stripped)


def _extract_first_json_object(text: str) -> str:
    start = text.find("{")
    if start == -1:
        return ""

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
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
    languages = {
        "python", "py", "javascript", "typescript", "json", "markdown", "md",
        "html", "css", "bash", "shell", "sh", "yaml", "yml", "toml", "ini",
        "cfg", "conf", "text", "txt",
    }

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
    if language in {"yaml", "yml"}:
        return any(":" in line or line.lstrip().startswith("- ") for line in code_lines)
    if language in {"toml", "ini", "cfg", "conf"}:
        return any("=" in line or line.strip().startswith("[") for line in code_lines)
    if language in {"markdown", "md"}:
        return any(line.lstrip().startswith(("#", "- ", "* ", ">", "```")) for line in code_lines)
    if language in {"text", "txt"}:
        return bool(code.strip())
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
    text = restore_rendered_code_blocks(text.replace("\u00a0", " "))
    code = get_code(text)
    if code is not None:
        text = code
    text = remove_line_numbers_keep_markdown(text)
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].strip().lower() in {
        "python", "py", "javascript", "typescript", "json", "markdown", "md",
        "html", "css", "yaml", "yml", "toml", "ini", "cfg", "conf", "text", "txt",
        "bash", "shell", "sh",
    }:
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
