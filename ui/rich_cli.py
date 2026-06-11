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

from bot import Agent
from utils.processor import remove_line_numbers_keep_markdown


class RichCLI:
    def __init__(self, agent: Agent):
        self.agent = agent
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
        logger = getattr(self.agent, "logger", None)
        if logger:
            logger.info("Rich CLI 增强版启动")
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

            # 显示用户消息（可选项，用简单方式显示）
            self.console.print(f"[bold green]你:[/bold green] {user_input}")

            # AI 思考状态
            status = self.console.status("[bold cyan]AI 正在思考...[/bold cyan]")
            status.start()
            try:
                assistant_reply = self.agent.run_turn(user_input)
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

        # 保存历史
        readline.write_history_file(".cli_history")
        self.agent.close()

    def _compress_memory(self):
        """手动触发记忆压缩：将工作记忆中最旧的消息对压缩为 LLM 摘要。"""
        status = self.console.status("[bold cyan]压缩中...[/bold cyan]")
        status.start()
        try:
            result = self.agent.compress_memory()
            status.stop()
            if result["status"] == "skipped":
                self.console.print(f"[yellow]{result['message']}[/yellow]")
                return
            self.console.print(
                f"[green]✓ 压缩完成。"
                f"已保留 {result['summaries']} 条摘要，"
                f"工作记忆剩余 {result['remaining']} 条消息。[/green]"
            )
        except Exception as e:
            status.stop()
            self.console.print(f"[red]压缩失败: {e}[/red]")