import re


def convert_markdown_to_plain_text(text: str) -> str:
    """将 Markdown 文本转换为纯文本，移除代码块、加粗、链接、表格等格式标记。"""
    text = strip_leading_line_numbers(text)
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


def extract_first_fenced_code_block(text: str) -> str | None:
    """从文本里找第一个 fenced code block。"""
    match = re.search(r"```(?:\w+)?\n(.*?)```", text, re.DOTALL)
    return match.group(1) if match else None


def strip_outer_code_fence(text: str, preserve_inner: bool = False) -> str:
    """剥掉包住整段文本的外层 fence"""
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        content = "\n".join(lines[1:-1])
        return content if preserve_inner else content.strip()
    return text


def strip_leading_line_numbers(text: str) -> str:
    """移除每行开头的行号（如 "1 "、"2."），不删除其他 Markdown 语法。"""
    return re.sub(r"^([ \t]*)\d{1,3}[ \t.、]+", r"\1", text, flags=re.MULTILINE)
