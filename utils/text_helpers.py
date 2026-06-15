# utils/text_helpers.py
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


def remove_line_numbers_keep_markdown(text: str) -> str:
    """
    仅移除每行开头的行号（如 "1 ", "2."），不删除代码块标记或其他 Markdown 语法。
    """
    return re.sub(r"^(\s*)\d{1,3}[\s.、]\s*", r"\1", text, flags=re.MULTILINE)


def strip_code_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


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
