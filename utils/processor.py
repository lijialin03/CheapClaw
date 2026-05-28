import markdown
from bs4 import BeautifulSoup
import re


def markdown_to_plain(text: str) -> str:
    """
    将 Markdown 文本转换为纯文本，移除所有格式标记（代码块、加粗、链接、表格等）。
    """
    # 1. 移除行号（行首的数字+分隔符）
    text = re.sub(r'^(\s*)\d{1,3}[\s.、]\s*', r'\1', text, flags=re.MULTILINE)
    
    # 2. 移除代码块标记，保留内容（不保留 ``` 符号）
    text = re.sub(r'```(?:\w+)?\n(.*?)\n```', r'\1', text, flags=re.DOTALL)
    
    # 3. 移除行内代码标记 `code`
    text = re.sub(r'`([^`]+)`', r'\1', text)
    
    # 4. 移除粗体 **bold** 或 __bold__
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    
    # 5. 移除斜体 *italic* 或 _italic_
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    
    # 6. 移除链接 [text](url) -> text
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    
    # 7. 移除图片 ![alt](url)
    text = re.sub(r'!\[[^\]]*\]\([^\)]+\)', '', text)
    
    # 8. 移除表格中的分隔线（如 |---|）和竖线
    text = re.sub(r'^\|[\s\-:]+\|$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\|', ' ', text)
    
    # 9. 移除标题标记（#、##等）
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    
    # 10. 清理多余的空行和空格
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = '\n'.join(line.strip() for line in text.splitlines())
    return text.strip()


def get_code(text):
    code_blocks = re.findall(r'```(?:\w+)?\n(.*?)```', text, re.DOTALL)
    if code_blocks:
        code = code_blocks[0]  # 获取第一个代码块的代码
        return code

def remove_line_numbers_keep_markdown(text: str) -> str:
    """
    仅移除每行开头的行号（如 "1 ", "2."），不删除代码块标记或其他 Markdown 语法。
    """
    # 匹配行首的缩进 + 数字 + 分隔符，保留缩进
    return re.sub(r'^(\s*)\d{1,3}[\s.、]\s*', r'\1', text, flags=re.MULTILINE)
