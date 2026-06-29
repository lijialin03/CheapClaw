import json
import re

_FENCED_JSON_PATTERN = re.compile(r"(?is)```\s*(?:json)?\s*\n(.*?)\n```")
_RENDERED_TYPE_PATTERN = re.compile(r'"type"\s*:\s*"(text|code)"')
_RENDERED_CONTENT_PATTERN = re.compile(r'"content"\s*:\s*"')
_RENDERED_NEXT_PART_PATTERN = re.compile(r'\n\s*}\s*,\s*\n\s*\{\s*\n\s*"type"\s*:')
_RENDERED_FINAL_PART_PATTERN = re.compile(r"\n\s*}\s*\n\s*]\s*\n\s*}\s*$")
_RENDERED_LANGUAGE_PATTERN = re.compile(r'"language"\s*:\s*"([^"\n\r]*)"')


def parse_structured_response(text: str) -> dict | None:
    data, _reason, _raw = diagnose_structured_response(text)
    return data


def diagnose_structured_response(text: str) -> tuple[dict | None, str, str]:
    """解析结构化响应，并返回归一化数据、诊断原因和实际解析文本。"""
    raw = extract_json_response_text(text)
    if not raw:
        return None, "no_json_candidate", ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        data, raw = _parse_json_with_fallbacks(raw)
        if data is None:
            return (
                None,
                f"json_decode_error:{exc.msg}:line={exc.lineno}:col={exc.colno}",
                raw,
            )
    return _validate_and_normalize_parts(data, raw)


def extract_json_response_text(text: str) -> str:
    """提取最可能的 JSON 响应：纯 JSON、fenced JSON，或文本中的首个对象。"""
    stripped = text.strip()
    if stripped.startswith("{"):
        return stripped
    match = _FENCED_JSON_PATTERN.fullmatch(stripped)
    if match:
        return match.group(1).strip()
    return _extract_first_json_object(stripped)


def _extract_first_json_object(text: str) -> str:
    """从混合文本中按括号配对提取首个 JSON object。"""
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


def _parse_json_with_fallbacks(raw: str) -> tuple[dict | None, str]:
    """标准 JSON 失败后，先修复字符串控制字符，再尝试 rendered 文本 fallback。"""
    repaired = _repair_json_string_control_chars(raw)
    if repaired != raw:
        try:
            return json.loads(repaired), repaired
        except json.JSONDecodeError:
            pass
    return _parse_rendered_structured_response(raw), raw


def _parse_rendered_structured_response(text: str) -> dict | None:
    """解析网页 innerText 可能破坏过换行与引号转义的结构化响应。"""
    if '"parts"' not in text:
        return None
    parts: list[dict] = []
    for match in _RENDERED_TYPE_PATTERN.finditer(text):
        part_type = match.group(1)
        content_match = _RENDERED_CONTENT_PATTERN.search(text, match.end())
        if not content_match:
            return None
        content_start = content_match.end()
        next_part = _RENDERED_NEXT_PART_PATTERN.search(text, content_start)
        final_part = _RENDERED_FINAL_PART_PATTERN.search(text, content_start)
        if next_part:
            content_end = next_part.start()
        elif final_part:
            content_end = final_part.start()
        else:
            return None
        content = _decode_rendered_json_string_content(text[content_start:content_end])
        part = {"type": part_type, "content": content}
        if part_type == "code":
            language_match = _RENDERED_LANGUAGE_PATTERN.search(
                text, match.end(), content_start
            )
            if language_match:
                part["language"] = language_match.group(1)
        parts.append(part)
    return {"parts": parts} if parts else None


def _validate_and_normalize_parts(
    data: object, raw: str
) -> tuple[dict | None, str, str]:
    """校验 parts schema，并只保留渲染层真正需要的字段。"""
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


def _decode_rendered_json_string_content(text: str) -> str:
    """解码 rendered fallback 中截取出的 JSON 字符串内容。"""
    content = text
    if content.endswith('"'):
        content = content[:-1]
    try:
        return json.loads(f'"{_repair_json_string_control_chars(content)}"')
    except json.JSONDecodeError:
        return content.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")


def _repair_json_string_control_chars(text: str) -> str:
    """修复 JSON 字符串内部未转义的换行、制表符和非结构性引号。"""
    result: list[str] = []
    in_string = False
    escaped = False
    changed = False
    for idx, char in enumerate(text):
        if in_string:
            if escaped:
                result.append(char)
                escaped = False
            elif char == "\\":
                result.append(char)
                escaped = True
            elif char == '"':
                if _is_structural_json_quote(text, idx):
                    result.append(char)
                    in_string = False
                else:
                    result.append('\\"')
                    changed = True
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


def _is_structural_json_quote(text: str, pos: int) -> bool:
    """判断当前引号是否是 JSON 结构边界，而不是字符串内容中的普通引号。"""
    peek = pos + 1
    while peek < len(text) and text[peek] in " \t\n\r":
        peek += 1
    return peek < len(text) and text[peek] in ":,}]"
