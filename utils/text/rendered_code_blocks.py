import re

RENDERED_CODE_LANGUAGE_LABELS = {
    "python",
    "py",
    "javascript",
    "typescript",
    "json",
    "markdown",
    "md",
    "html",
    "css",
    "bash",
    "shell",
    "sh",
    "yaml",
    "yml",
    "toml",
    "ini",
    "cfg",
    "conf",
    "text",
    "txt",
}
RENDERED_CODE_LANGUAGE_LABELS_BY_LENGTH = tuple(
    sorted(RENDERED_CODE_LANGUAGE_LABELS, key=len, reverse=True)
)

# 网页代码块工具栏按钮的 innerText 可能拼接到语言标签后。
RENDERED_CODE_TOOLBAR_BUTTONS = ("copy", "download", "run")


def restore_rendered_code_blocks(text: str) -> str:
    """将网页 inner_text 中的“语言名 + 行号列 + 代码”还原为 Markdown 代码块。"""
    lines = text.splitlines()
    restored: list[str] = []
    index = 0

    while index < len(lines):
        # 网页 innerText 中的代码块通常呈现为：语言标签、行号列、
        # 与行号数量相同的代码行。只有完整匹配该模式时才还原。
        language = normalize_rendered_language_label(lines[index].strip())
        if language not in RENDERED_CODE_LANGUAGE_LABELS:
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


def normalize_rendered_language_label(label: str) -> str:
    """剥离网页代码块工具栏按钮文本（如 htmlCopyDownloadRun → html）。"""
    lowered = label.lower()
    while True:
        changed = False
        for button in RENDERED_CODE_TOOLBAR_BUTTONS:
            if lowered.endswith(button):
                lowered = lowered[: -len(button)]
                changed = True
        if not changed:
            break
    return lowered


def strip_inline_toolbar_prefix(line: str) -> str:
    """剥离粘连到首行代码前的网页代码块工具栏文本。"""
    stripped = line.lstrip()
    # 只匹配 Copy/Download/Run 这类 UI 按钮文本，避免误伤
    # "htmlcopySomething" 这样的普通内容。
    has_toolbar_label = any(
        button.title() in stripped for button in RENDERED_CODE_TOOLBAR_BUTTONS
    )
    if not has_toolbar_label:
        return line
    lowered = stripped.lower()
    for language in RENDERED_CODE_LANGUAGE_LABELS_BY_LENGTH:
        if not lowered.startswith(language):
            continue
        remainder = stripped[len(language) :]
        for button in RENDERED_CODE_TOOLBAR_BUTTONS:
            label = button.title()
            trimmed = remainder.lstrip()
            if trimmed.startswith(label):
                remainder = trimmed[len(label) :]
        return remainder.lstrip() if remainder.strip() else line
    return line


def _looks_like_rendered_code(language: str, code_lines: list[str]) -> bool:
    # 这些启发式用于避免把以语言名开头的普通文本误还原为代码块。
    code = "\n".join(code_lines)
    if language in {"python", "py"}:
        return looks_like_python_code(code) or any(
            token in code for token in ("=", "with ", "open(", 'b"', "print(")
        )
    if language in {"yaml", "yml"}:
        return any(":" in line or line.lstrip().startswith("- ") for line in code_lines)
    if language in {"toml", "ini", "cfg", "conf"}:
        return any("=" in line or line.strip().startswith("[") for line in code_lines)
    if language in {"markdown", "md"}:
        return any(
            line.lstrip().startswith(("#", "- ", "* ", ">", "```"))
            for line in code_lines
        )
    if language in {"text", "txt"}:
        return bool(code.strip())
    return bool(
        re.search(
            r"[{}();=<>]|^\s*(const|let|var|function|import|export|class|def|if|for|while)\b",
            code,
            re.MULTILINE,
        )
    )


def looks_like_python_code(text: str) -> bool:
    return bool(
        re.search(
            r"(?m)^(from\s+\S+\s+import\s+|import\s+\S+|class\s+\w+|def\s+\w+)", text
        )
    )
