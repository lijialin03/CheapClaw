# ui/rich_cli.py
from dataclasses import dataclass
import re
import readline

from pygments.util import ClassNotFound
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from agent_core import Agent
from ui.command_handler import CLICommandHandler
from utils.text_helpers import parse_structured_response, remove_line_numbers_keep_markdown, restore_rendered_code_blocks


@dataclass(frozen=True)
class TurnProgressEntry:
    event_type: str
    message: str
    detail: str = ""
    dedupe_key: str = ""


class TurnProgress:
    def __init__(self) -> None:
        self.entries: list[TurnProgressEntry] = []
        self._seen_keys: set[str] = set()
        self._current_key = ""

    def add(self, entry: TurnProgressEntry | None) -> bool:
        if entry is None:
            return False
        key = entry.dedupe_key or f"{entry.event_type}:{entry.message}:{entry.detail}"
        if key == self._current_key:
            return False
        self._current_key = key
        if key in self._seen_keys:
            return False
        self._seen_keys.add(key)
        self.entries.append(entry)
        return True

    def render(self, finished: bool = False) -> Text:
        if not self.entries:
            return Text()

        output = Text()
        for index, entry in enumerate(self.entries):
            is_current = index == len(self.entries) - 1 and not finished
            if index:
                output.append("\n")
            if is_current:
                output.append("• ", style="bold cyan")
                output.append(entry.message, style="bold cyan")
                if entry.detail:
                    output.append("\n  └─ ", style="cyan")
                    output.append(entry.detail, style="cyan")
            else:
                output.append("✓ ", style="dim green")
                output.append(entry.message, style="dim")
        return output


class RichCLI:
    HISTORY_FILE = ".cheapclaw/.cli_history"
    HELP_TEXT = "[dim]↑↓ 历史记录 | /help 帮助 | /clear 清屏 | /compress 压缩记忆 | /memory 记忆状态 | /exit 退出[/dim]"
    CODE_BLOCK_PATTERN = r'(?s)```(\w+)?\n(.*?)```'
    STRUCTURED_MARKDOWN_PATTERN = r"(?m)^(#{1,6}\s+|\s*[-*+]\s+|\s*\d+[.)]\s+|>\s+|\|.*\|\s*$)"
    PROGRESS_MESSAGES = {
        "tool_routing": "正在判断是否需要读取本地信息...",
        "tool_planning": "正在规划本地读取步骤...",
        "file_transporting": "正在长内容通过文件发送...",
        "file_edit_drafting": "正在生成文件修改草稿...",
        "auto_compressing": "正在自动压缩历史记忆...",
        "auto_trimming": "正在整理超限工作记忆...",
    }
    PROGRESS_FORMATTERS = {
        "tool_running_command": lambda event: ("运行命令", event.get("command", "")),
        "tool_changing_dir": lambda event: ("切换目录", event.get("command", "")),
        "auto_compressed": lambda event: (
            "已自动压缩历史记忆",
            f"归档 {event.get('removed', 0)} 条消息，当前 {event.get('summaries', 0)} 条摘要，剩余 {event.get('remaining', 0)} 条",
        ),
        "auto_trimmed": lambda event: (
            "已整理超限工作记忆",
            f"丢弃 {event.get('removed', 0)} 条较早消息，剩余 {event.get('remaining', 0)} 条",
        ),
    }

    def __init__(self, agent: Agent, title: str = "AI 对话助手", input_label: str = "你"):
        self.agent = agent
        self.title = title
        self.input_prompt = f"\001\033[1;32m\002{input_label}> \001\033[0m\002"
        self.console = Console(force_terminal=True)  # 强制 ANSI 转义，提高兼容性
        self.command_handler = CLICommandHandler(agent, self.console, self.HELP_TEXT)
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

        self._display_startup_notices()
        self.console.print(self.HELP_TEXT)
        return True

    def _display_startup_notices(self) -> None:
        for notice in self.agent.consume_notices():
            message = str(notice.get("message") or "").strip()
            if not message:
                continue
            level = notice.get("level") or "info"
            if level == "warning":
                self.console.print(Panel(message, title="⚠️  登录状态警告", style="red", border_style="red"))
            else:
                self.console.print(Panel(message, title="💡 注意", style="cyan", border_style="cyan"))

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
        return self.command_handler.handle(user_input)

    def _run_agent_turn(self, user_input: str) -> None:
        progress = TurnProgress()
        try:
            with Live(progress.render(), console=self.console, refresh_per_second=8, transient=True) as live:
                assistant_reply = self.agent.run_turn(user_input, self._build_status_event_handler(live, progress))
        except Exception as e:
            self.console.print(f"[red]❌ 出错了: {e}[/red]")
            return

        self._display_memory_events(self.agent.consume_events())
        self._display_finished_progress(progress)
        self.console.print("\n[bold blue]AI:[/bold blue]")
        self._display_ai_response(assistant_reply)
        self.console.print()

    def _shutdown(self) -> None:
        with self.console.status("[bold cyan]正在保存记忆并退出...[/bold cyan]"):
            self._save_readline_history()
            self._cleanup_tool_checkpoints()
            self.agent.close()

    def _build_status_event_handler(self, live: Live, progress: TurnProgress):
        def update(event: dict) -> None:
            if progress.add(self._progress_entry_for_event(event)):
                live.update(progress.render(), refresh=True)

        return update

    def _progress_entry_for_event(self, event: dict) -> TurnProgressEntry | None:
        event_type = event.get("type", "")
        if event_type in self.PROGRESS_FORMATTERS:
            message, detail = self.PROGRESS_FORMATTERS[event_type](event)
            if not detail:
                return None
            return TurnProgressEntry(
                event_type=event_type,
                message=message,
                detail=detail,
                dedupe_key=f"{event_type}:{detail}",
            )
        message = self.PROGRESS_MESSAGES.get(event_type)
        if not message:
            return None
        return TurnProgressEntry(event_type=event_type, message=message, dedupe_key=event_type)

    def _display_finished_progress(self, progress: TurnProgress) -> None:
        if progress.entries:
            self.console.print(progress.render(finished=True))

    def _display_memory_events(self, events: list[dict]) -> None:
        for event in events:
            if event.get("type") not in {"auto_compressed", "auto_trimmed"}:
                continue

    def _display_ai_response(self, text: str) -> None:
        """
        显示 AI 回复：
        - 普通文本直接输出
        - 代码块用 rich.syntax 高亮，去除行号
        - 支持多个代码块，保持原文顺序
        """
        restored = restore_rendered_code_blocks(text)
        if self._display_structured_response(text) or self._display_structured_response(restored):
            return

        cleaned = remove_line_numbers_keep_markdown(restored)
        last_end = 0

        for match in re.finditer(self.CODE_BLOCK_PATTERN, cleaned):
            start, end = match.span()
            self._print_plain_text(cleaned[last_end:start])
            self._print_code_block(match.group(2), lang=match.group(1) or "python")
            last_end = end

        self._print_plain_text(cleaned[last_end:])

    def _display_structured_response(self, text: str) -> bool:
        data = parse_structured_response(text)
        if not data:
            return False
        for part in data["parts"]:
            part_type = part.get("type")
            content = str(part.get("content") or "")
            if part_type == "code":
                self._print_code_block(content, lang=str(part.get("language") or "text"))
            else:
                self._print_plain_text(content)
        return True

    def _print_plain_text(self, text: str) -> None:
        plain = text.strip()
        if not plain:
            return
        if self._looks_like_structured_markdown(plain):
            self.console.print(Markdown(plain, justify="left"))
        else:
            self.console.print(Text(plain), soft_wrap=True)

    def _looks_like_structured_markdown(self, text: str) -> bool:
        return bool(re.search(self.STRUCTURED_MARKDOWN_PATTERN, text))

    def _print_code_block(self, code: str, lang: str = "python") -> None:
        lexer = lang or "text"
        try:
            syntax = Syntax(code.strip(), lexer=lexer, theme="monokai", line_numbers=False)
        except ClassNotFound:
            syntax = Syntax(code.strip(), lexer="text", theme="monokai", line_numbers=False)
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
        with open(self.HISTORY_FILE, "r+", encoding="utf-8") as file:
            lines = file.readlines()[-readline.get_history_length():]
            file.seek(0)
            file.truncate()
            file.writelines(lines)

    def _cleanup_tool_checkpoints(self) -> None:
        tool_orchestrator = getattr(self.agent, "tool_orchestrator", None)
        tool_runner = getattr(tool_orchestrator, "tool_runner", None)
        cleanup = getattr(tool_runner, "cleanup_checkpoints", None)
        if callable(cleanup):
            cleanup()
