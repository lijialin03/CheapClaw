# ui/rich_cli.py
import re
import readline
from datetime import datetime

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.syntax import Syntax
from rich.text import Text
from rich import box

from llm.qwen import QwenClient
from bot.assembler import Assembler
from bot.memory import Memory
from utils.processor import markdown_to_plain, remove_line_numbers_keep_markdown


class RichCLI:
    def __init__(self, client: QwenClient, assembler: Assembler, memory: Memory):
        self.client = client
        self.assembler = assembler
        self.memory = memory
        self.console = Console(force_terminal=True)  # 强制 ANSI 转义，提高兼容性
        self._setup_readline()

    def _setup_readline(self):
        """配置命令行历史"""
        histfile = ".cli_history"
        try:
            readline.read_history_file(histfile)
        except FileNotFoundError:
            pass
        readline.set_history_length(500)

    def _display_ai_response(self, text: str) -> None:
        """
        显示 AI 回复：
        - 普通文本直接输出
        - 代码块用 rich.syntax 高亮，去除行号
        - 支持多个代码块，保持原文顺序
        """
        # 先移除行号（保留 Markdown 结构）
        cleaned = remove_line_numbers_keep_markdown(text)

        # 正则匹配代码块：```language\n code \n```
        pattern = r'(?s)```(\w+)?\n(.*?)```'
        last_end = 0

        for match in re.finditer(pattern, cleaned):
            start, end = match.span()
            # 输出代码块之前的普通文本
            if start > last_end:
                plain = cleaned[last_end:start].strip()
                if plain:
                    self.console.print(plain)

            # 处理代码块
            lang = match.group(1) or "python"   # 默认 python
            code = match.group(2).strip()
            # 使用语法高亮，不显示行号
            syntax = Syntax(code, lexer=lang, theme="monokai", line_numbers=False)
            self.console.print(syntax)

            last_end = end

        # 输出最后一个代码块之后的普通文本
        if last_end < len(cleaned):
            plain = cleaned[last_end:].strip()
            if plain:
                self.console.print(plain)

    def run(self):
        self.client.logger.info("Rich CLI 增强版启动")
        self.console.print(Panel.fit("🤖 通义千问对话助手", style="bold cyan", border_style="cyan"))
        self.console.print("[dim]↑↓ 历史记录 | /clear 清屏 | /compress 压缩记忆 | /exit 退出[/dim]")

        while True:
            try:
                user_input = Prompt.ask("\n[bold green]你[/bold green]")
            except (KeyboardInterrupt, EOFError):
                self.console.print("\n[yellow]再见！[/yellow]")
                break

            if not user_input:
                continue
            if user_input.lower() == "/exit":
                break
            if user_input.lower() == "/clear":
                self.console.clear()
                continue
            if user_input.lower() == "/compress":
                self._compress_memory()
                continue

            # 组装 prompt（包含系统设定、历史记忆、当前消息）
            prompt = self.assembler.assemble(user_input)

            # 显示用户消息（可选项，用简单方式显示）
            self.console.print(f"[bold green]你:[/bold green] {user_input}")

            # AI 思考状态
            status = self.console.status("[bold cyan]AI 正在思考...[/bold cyan]")
            status.start()
            try:
                assistant_reply = self.client.send_text(prompt)
            except Exception as e:
                self.console.print(f"[red]❌ 出错了: {e}[/red]")
                status.stop()
                continue
            status.stop()

            # 显示 AI 回复
            self.console.print("\n[bold blue]AI:[/bold blue]")
            self._display_ai_response(assistant_reply)
            # 添加空行分隔
            self.console.print()

            # 存储纯文本记忆（用于上下文）
            clean_text = markdown_to_plain(assistant_reply)
            self.assembler.update_memory(user_input, clean_text)

        # 保存历史
        readline.write_history_file(".cli_history")
        self.client.close()

    def _compress_memory(self):
        """手动触发记忆压缩：将工作记忆中最旧的消息对压缩为 LLM 摘要。"""
        # 先检查 buffer 是否有足够消息可压缩
        msg_count = len(self.memory.buffer.messages)
        if msg_count < 4:
            self.console.print("[yellow]消息不足（至少需要 2 轮对话），暂无需压缩[/yellow]")
            return

        self.console.print("[cyan]正在压缩历史记忆（调用 LLM 生成摘要）...[/cyan]")
        status = self.console.status("[bold cyan]压缩中...[/bold cyan]")
        status.start()
        try:
            self.memory.compress_with_summary()
            self.memory.save()
            status.stop()
            summaries = len(self.memory.compressor.summaries)
            remaining = len(self.memory.buffer.messages)
            self.console.print(
                f"[green]✓ 压缩完成。"
                f"已保留 {summaries} 条摘要，"
                f"工作记忆剩余 {remaining} 条消息。[/green]"
            )
        except Exception as e:
            status.stop()
            self.console.print(f"[red]压缩失败: {e}[/red]")