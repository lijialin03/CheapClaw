"""尚未归入更具体模块的小型通用文本工具。"""


def first_nonempty_line(text: str) -> str:
    """返回文本中的第一行非空内容。"""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def truncate_text_fields(data: dict, fields: tuple[str, ...], max_chars: int) -> dict:
    """复制字典，并截断指定字段中过长的字符串值。"""
    compact = dict(data)
    for field in fields:
        value = compact.get(field)
        if isinstance(value, str) and len(value) > max_chars:
            compact[field] = value[:max_chars] + "\n...[truncated]"
            compact[f"truncated_{field}_chars"] = len(value) - max_chars
    return compact
