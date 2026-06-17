# ui/command_handler.py
from rich.console import Console
from rich.panel import Panel

from agent_core import Agent


class CLICommandHandler:
    def __init__(self, agent: Agent, console: Console, help_text: str):
        self.agent = agent
        self.console = console
        self.help_text = help_text

    def handle(self, user_input: str) -> str:
        command = user_input.lower()
        if command == "/exit":
            return "exit"
        if command == "/clear":
            self.console.clear()
            return "handled"
        if command == "/compress":
            self._compress_memory()
            return "handled"
        if command.startswith("/memory"):
            self._handle_memory_command(user_input)
            return "handled"
        if command == "/help":
            self.console.print(self.help_text)
            return "handled"
        return "unhandled"

    def _handle_memory_command(self, user_input: str) -> None:
        parts = user_input.split(maxsplit=2)
        subcommand = parts[1].lower() if len(parts) >= 2 else "status"
        if subcommand == "preview":
            query = parts[2] if len(parts) >= 3 else ""
            self._show_memory_preview(query)
            return
        if subcommand == "clear-session":
            self._clear_session_memory()
            return
        if subcommand in {"status", ""}:
            self._show_memory_status()
            return
        self.console.print("[yellow]用法: /memory | /memory preview <query> | /memory clear-session[/yellow]")

    def _show_memory_status(self) -> None:
        status = self.agent.memory_status()
        lines = [
            f"存储路径: {status.get('path') or '未启用'}",
            f"当前 session: {status.get('session_messages', 0)} 条消息，约 {status.get('session_tokens', 0)} tokens",
            f"持久摘要: {status.get('summary_count', 0)} 条",
            f"关键信息: {status.get('key_info_count', 0)} 条",
        ]
        last_session = status.get("last_session") or {}
        if last_session:
            lines.append(
                "上次关闭: "
                f"{last_session.get('message_count', 0)} 条消息，"
                f"压缩={'是' if last_session.get('compressed') else '否'}"
            )
        self.console.print(Panel("\n".join(lines), title="记忆状态", border_style="cyan"))

    def _show_memory_preview(self, query: str) -> None:
        preview = self.agent.memory_preview(query=query)
        lines = [
            f"查询: {preview.get('query') or '(空)'}",
            f"预算: {preview.get('budget', 0)} tokens",
            f"选择关键信息: {preview.get('selected_key_info', 0)} 条 / {preview.get('key_info_tokens', 0)} tokens",
            f"选择历史摘要: {preview.get('selected_summaries', 0)} 条 / {preview.get('summary_tokens', 0)} tokens",
            f"最近对话: {preview.get('recent_messages', 0)} 条 / {preview.get('recent_tokens', 0)} tokens",
            f"总计: {preview.get('total_tokens', 0)} tokens",
        ]
        self.console.print(Panel("\n".join(lines), title="记忆预览", border_style="cyan"))

    def _clear_session_memory(self) -> None:
        result = self.agent.clear_session_memory()
        self.console.print(f"[green]已清空当前 session 工作记忆：{result['cleared']} 条消息。[/green]")

    def _compress_memory(self):
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
