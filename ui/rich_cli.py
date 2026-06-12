# ui/rich_cli.py
import re
import readline
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from bot import Agent
from utils.processor import remove_line_numbers_keep_markdown


class RichCLI:
    INPUT_PROMPT = "\001\033[1;32m\002你> \001\033[0m\002"

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

    def _update_status_for_event(self, status):
        def update(event: dict) -> None:
            if event.get("type") == "auto_compressing":
                status.update("[bold cyan]正在自动压缩历史记忆...[/bold cyan]")
            elif event.get("type") == "auto_trimming":
                status.update("[bold yellow]工作记忆超限，正在整理历史...[/bold yellow]")
            elif event.get("type") == "file_transporting":
                status.update("[bold cyan]内容较长，正在以文件形式发送...[/bold cyan]")

        return update

    def _display_events(self, events: list[dict]) -> None:
        for event in events:
            if event.get("type") == "auto_compressed":
                self.console.print(
                    f"[dim cyan]已自动压缩历史记忆：归档 {event['removed']} 条消息，"
                    f"当前 {event['summaries']} 条摘要，工作记忆剩余 {event['remaining']} 条。[/dim cyan]"
                )
            elif event.get("type") == "auto_trimmed":
                self.console.print(
                    f"[dim yellow]工作记忆超限，已自动丢弃 {event['removed']} 条较早消息，"
                    f"剩余 {event['remaining']} 条。[/dim yellow]"
                )

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
        with self.console.status("[bold cyan]正在启动浏览器客户端...[/bold cyan]"):
            try:
                self.agent.start()
            except Exception as e:
                self.console.print(f"[red]启动失败: {e}[/red]")
                self.agent.close()
                return
        self.console.print("[dim]↑↓ 历史记录 | /clear 清屏 | /compress 压缩记忆 | /exit 退出[/dim]")

        while True:
            try:
                user_input = input(f"\n{self.INPUT_PROMPT}")
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
            if user_input.startswith("/"):
                self._handle_agent_command(user_input)
                continue

            # AI 思考状态
            status = self.console.status("[bold cyan]AI 正在思考...[/bold cyan]")
            status.start()
            try:
                assistant_reply = self.agent.run_turn(user_input, self._update_status_for_event(status))
            except Exception as e:
                self.console.print(f"[red]❌ 出错了: {e}[/red]")
                status.stop()
                continue
            status.stop()

            self._display_events(self.agent.consume_events())

            # 显示 AI 回复
            self.console.print("\n[bold blue]AI:[/bold blue]")
            self._display_ai_response(assistant_reply)
            # 添加空行分隔
            self.console.print()

        # 保存历史
        readline.write_history_file(".cli_history")
        self.agent.close()

    def _handle_agent_command(self, user_input: str) -> None:
        status = self.console.status("[bold cyan]执行命令中...[/bold cyan]")
        status.start()
        try:
            result = self.agent.handle_command(user_input, self._update_status_for_event(status))
        except Exception as e:
            status.stop()
            self.console.print(f"[red]命令失败: {e}[/red]")
            return
        status.stop()
        self._display_events(self.agent.consume_events())
        self._display_command_result(result)

    def _display_command_result(self, result: dict) -> None:
        result_type = result.get("type")
        if result_type == "error":
            self.console.print(f"[red]{result.get('message', '命令失败')}[/red]")
        elif result_type == "file":
            path = result.get("path", "")
            content = result.get("content", "")
            self.console.print(Panel(content or "[dim]空文件[/dim]", title=path, border_style="cyan"))
        elif result_type == "list":
            table = Table(title=f"目录: {result.get('path', '.')}")
            table.add_column("类型", style="cyan")
            table.add_column("大小", justify="right")
            table.add_column("路径")
            for entry in result.get("entries", []):
                table.add_row(entry["type"], str(entry["size"]), entry["path"])
            self.console.print(table)
        elif result_type == "assistant":
            self.console.print("\n[bold blue]AI:[/bold blue]")
            self._display_ai_response(result.get("content", ""))
            self.console.print()
        elif result_type == "edit_plan":
            plan = result.get("plan", {})
            self.console.print(f"[bold cyan]Staged edit:[/bold cyan] {plan.get('path', '')}")
            if plan.get("reason"):
                self.console.print(f"[dim]{plan['reason']}[/dim]")
            diff = result.get("diff") or ""
            if diff:
                self.console.print(Syntax(diff, lexer="diff", theme="monokai", line_numbers=False))
            self.console.print("[dim]使用 /apply 应用，或 /discard 丢弃。[/dim]")
        elif result_type == "text":
            self.console.print(result.get("content", ""))
        else:
            self.console.print(str(result))

    def _compress_memory(self):
        """手动触发记忆压缩：将工作记忆中最旧的消息对压缩为 LLM 摘要。"""
        status = self.console.status("[bold cyan]压缩中...[/bold cyan]")
        status.start()
        try:
            result = self.agent.compress_memory()
            status.stop()
            if result["status"] in {"skipped", "failed"}:
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