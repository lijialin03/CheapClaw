# ui/rich_cli.py
import re
import readline
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from agent_core import Agent
from utils.text_helpers import remove_line_numbers_keep_markdown


class RichCLI:
    HISTORY_FILE = ".cli_history"
    HELP_TEXT = "[dim]↑↓ 历史记录 | /clear 清屏 | /compress 压缩记忆 | /exit 退出[/dim]"
    CODE_BLOCK_PATTERN = r'(?s)```(\w+)?\n(.*?)```'
    STATUS_MESSAGES = {
        "auto_compressing": "[bold cyan]正在自动压缩历史记忆...[/bold cyan]",
        "auto_trimming": "[bold yellow]工作记忆超限，正在整理历史...[/bold yellow]",
        "file_transporting": "[bold cyan]内容较长，正在以文件形式发送...[/bold cyan]",
        "tool_routing": "[bold cyan]正在判断是否需要读取本地信息...[/bold cyan]",
        "tool_planning": "[bold cyan]正在规划需要读取的信息...[/bold cyan]",
        "file_edit_drafting": "[bold cyan]正在生成文件修改内容...[/bold cyan]",
    }
    STATUS_FORMATTERS = {
        "tool_running_command": lambda event: f"[bold cyan]正在运行命令: {event.get('command', '')}[/bold cyan]",
        "tool_changing_dir": lambda event: f"[bold cyan]正在切换目录: {event.get('command', '')}[/bold cyan]",
    }

    def __init__(self, agent: Agent, title: str = "AI 对话助手", input_label: str = "你"):
        self.agent = agent
        self.title = title
        self.input_prompt = f"\001\033[1;32m\002{input_label}> \001\033[0m\002"
        self.console = Console(force_terminal=True)  # 强制 ANSI 转义，提高兼容性
        self._setup_readline()

    def run(self):
        if not self._start_agent():
            return

        try:
            self._run_input_loop()
        finally:
            self._shutdown()

    def _start_agent(self) -> bool:
        logger = getattr(self.agent, "logger", None)
        if logger:
            logger.info("Rich CLI 增强版启动")

        self.console.print(Panel.fit(f"🤖 {self.title}", style="bold cyan", border_style="cyan"))
        with self.console.status("[bold cyan]正在启动模型客户端...[/bold cyan]"):
            try:
                self.agent.start()
            except Exception as e:
                self.console.print(f"[red]启动失败: {e}[/red]")
                self.agent.close()
                return False

        self.console.print(self.HELP_TEXT)
        return True

    def _run_input_loop(self) -> None:
        while True:
            user_input = self._read_user_input()
            if user_input is None:
                break
            if not user_input:
                continue

            command_result = self._handle_command(user_input)
            if command_result == "exit":
                break
            if command_result == "handled":
                continue

            self._run_agent_turn(user_input)

    def _read_user_input(self) -> str | None:
        try:
            return input(f"\n{self.input_prompt}")
        except (KeyboardInterrupt, EOFError):
            self.console.print("\n[yellow]再见！[/yellow]")
            return None

    def _handle_command(self, user_input: str) -> str:
        command = user_input.lower()
        if command == "/exit":
            return "exit"
        if command == "/clear":
            self.console.clear()
            return "handled"
        if command == "/compress":
            self._compress_memory()
            return "handled"
        return "unhandled"

    def _run_agent_turn(self, user_input: str) -> None:
        status = self.console.status("[bold cyan]AI 正在思考...[/bold cyan]")
        status.start()
        try:
            assistant_reply = self.agent.run_turn(user_input, self._build_status_event_handler(status))
        except Exception as e:
            status.stop()
            self.console.print(f"[red]❌ 出错了: {e}[/red]")
            return
        status.stop()

        self._display_events(self.agent.consume_events())
        self.console.print("\n[bold blue]AI:[/bold blue]")
        self._display_ai_response(assistant_reply)
        self.console.print()

    def _shutdown(self) -> None:
        self._save_readline_history()
        self._cleanup_tool_checkpoints()
        self.agent.close()

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

    def _build_status_event_handler(self, status):
        def update(event: dict) -> None:
            message = self._status_message_for_event(event)
            if message:
                status.update(message)

        return update

    def _status_message_for_event(self, event: dict) -> str:
        event_type = event.get("type")
        if event_type in self.STATUS_FORMATTERS:
            return self.STATUS_FORMATTERS[event_type](event)
        return self.STATUS_MESSAGES.get(event_type, "")

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
        cleaned = remove_line_numbers_keep_markdown(text)
        last_end = 0

        for match in re.finditer(self.CODE_BLOCK_PATTERN, cleaned):
            start, end = match.span()
            self._print_plain_text(cleaned[last_end:start])
            self._print_code_block(match.group(2), lang=match.group(1) or "python")
            last_end = end

        self._print_plain_text(cleaned[last_end:])

    def _print_plain_text(self, text: str) -> None:
        plain = text.strip()
        if plain:
            self.console.print(plain)

    def _print_code_block(self, code: str, lang: str = "python") -> None:
        syntax = Syntax(code.strip(), lexer=lang, theme="monokai", line_numbers=False)
        self.console.print(syntax)

    def _setup_readline(self):
        """配置命令行历史"""
        try:
            readline.read_history_file(self.HISTORY_FILE)
        except FileNotFoundError:
            pass
        readline.set_history_length(500)

    def _save_readline_history(self) -> None:
        readline.write_history_file(self.HISTORY_FILE)

    def _cleanup_tool_checkpoints(self) -> None:
        tool_orchestrator = getattr(self.agent, "tool_orchestrator", None)
        tool_runner = getattr(tool_orchestrator, "tool_runner", None)
        cleanup = getattr(tool_runner, "cleanup_checkpoints", None)
        if callable(cleanup):
            cleanup()
