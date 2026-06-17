# agent_core/agent.py
from pathlib import Path
from typing import Any, Callable, Optional

from .config import AgentConfig
from .file_transport import PromptTransport
from .memory import Memory
from .prompt_loader import render_prompt
from .tool_commands import ReadonlyToolCommandRunner
from .tool_orchestrator import ToolOrchestrator
from utils.text_helpers import markdown_to_plain


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Agent:
    """轻量对话编排层，集中管理一轮对话、记忆压缩和客户端清理。"""

    def __init__(
        self,
        client: Any,
        memory: Memory,
        workspace: Any = None,
        config: AgentConfig | None = None,
        max_text_chars: int | None = None,
        upload_dir: str | Path | None = None,
        file_transport_enabled: bool | None = None,
        tool_orchestration_enabled: bool | None = None,
        max_tool_steps: int | None = None,
        tool_runner: ReadonlyToolCommandRunner | None = None,
    ):
        config = config or AgentConfig()
        self.client = client
        self.memory = memory
        self.workspace = workspace
        self.max_text_chars = config.max_text_chars if max_text_chars is None else max_text_chars
        resolved_upload_dir = upload_dir if upload_dir is not None else config.upload_dir
        self.upload_dir = Path(resolved_upload_dir) if resolved_upload_dir else PROJECT_ROOT / ".cheapclaw" / "uploads"
        self.file_transport_enabled = (
            config.file_transport_enabled if file_transport_enabled is None else file_transport_enabled
        )
        self.tool_orchestration_enabled = (
            config.tool_orchestration_enabled if tool_orchestration_enabled is None else tool_orchestration_enabled
        )
        resolved_max_tool_steps = config.max_tool_steps if max_tool_steps is None else max_tool_steps
        self.max_tool_steps = max(1, int(resolved_max_tool_steps))
        self.tool_config = config.tools
        self.tool_runner = self._build_tool_runner(tool_runner)
        self.transport = self._build_transport()
        self.tool_orchestrator = self._build_tool_orchestrator()
        self.logger = getattr(client, "logger", None)
        self._closed = False

    def _build_tool_runner(self, tool_runner: ReadonlyToolCommandRunner | None) -> ReadonlyToolCommandRunner | None:
        if tool_runner is not None:
            return tool_runner
        if self.workspace is None:
            return None
        return ReadonlyToolCommandRunner(self.workspace, config=self.tool_config)

    def _build_transport(self) -> PromptTransport:
        return PromptTransport(
            client=self.client,
            upload_dir=self.upload_dir,
            max_text_chars=self.max_text_chars,
            file_transport_enabled=self.file_transport_enabled,
            emit_event=self._emit_event,
        )

    def _build_tool_orchestrator(self) -> ToolOrchestrator | None:
        if self.tool_runner is None:
            return None
        return ToolOrchestrator(
            client=self.client,
            tool_runner=self.tool_runner,
            max_tool_steps=self.max_tool_steps,
            emit_event=self._emit_event,
            config=self.tool_config,
        )

    def start(self) -> None:
        start = getattr(self.client, "start", None)
        if callable(start):
            start()

    def run_turn(self, user_input: str, event_callback: Optional[Callable[[dict], None]] = None) -> str:
        """执行一轮对话，并将清洗后的回复写入记忆。"""
        if self.tool_orchestrator is not None and self.tool_orchestrator.has_pending_confirmation():
            assistant_reply = self.tool_orchestrator.handle_pending_command_confirmation(user_input, event_callback)
            self._update_memory(user_input, assistant_reply, event_callback)
            return assistant_reply

        if not self._can_use_tool_orchestration():
            return self._run_legacy_turn(user_input, event_callback)

        assistant_reply = self.tool_orchestrator.run_turn(user_input, event_callback)
        if assistant_reply is None or self._is_router_sentinel_reply(assistant_reply):
            return self._run_legacy_turn(user_input, event_callback)

        self._update_memory(user_input, assistant_reply, event_callback)
        return assistant_reply

    def consume_events(self) -> list[dict]:
        return self.memory.consume_events()

    def compress_memory(self) -> dict:
        """手动压缩历史记忆，返回 UI 可展示的结构化状态。"""
        msg_count = len(self.memory.buffer.messages)
        if msg_count < 4:
            return {
                "status": "skipped",
                "message": "消息不足（至少需要 2 轮对话），暂无需压缩",
                "message_count": msg_count,
            }

        compressed = self.memory.compress_with_summary()
        if not compressed:
            return {
                "status": "failed",
                "message": "压缩未生成摘要，已保留原始工作记忆",
                "message_count": len(self.memory.buffer.messages),
            }
        self.memory.save()
        return {
            "status": "completed",
            "summaries": len(self.memory.compressor.summaries),
            "remaining": len(self.memory.buffer.messages),
        }

    def memory_status(self) -> dict:
        return self.memory.stats()

    def memory_preview(self, query: str = "") -> dict:
        return self.memory.preview_context(query=query)

    def clear_session_memory(self) -> dict:
        return self.memory.clear_session()

    def close(self) -> None:
        """先清理 session 记忆，再释放底层客户端资源。"""
        if self._closed:
            return
        self._closed = True
        close = getattr(self.client, "close", None)
        try:
            self.memory.close_session()
        finally:
            if callable(close):
                close()

    def _run_legacy_turn(self, user_input: str, event_callback: Optional[Callable[[dict], None]] = None) -> str:
        prompt = self._build_chat_prompt(user_input)
        assistant_reply = self.transport.send(prompt, event_callback)
        self._update_memory(user_input, assistant_reply, event_callback)
        return assistant_reply

    def _build_chat_prompt(self, user_input: str) -> str:
        memory_context = self.memory.get_context(query=user_input)
        memory_section = f"{memory_context}\n\n" if memory_context else ""
        return render_prompt(
            "chat.md",
            memory_section=memory_section,
            user_input=user_input,
        )

    def _can_use_tool_orchestration(self) -> bool:
        return self.tool_orchestration_enabled and self.workspace is not None and self.tool_orchestrator is not None

    def _is_router_sentinel_reply(self, assistant_reply: str) -> bool:
        return assistant_reply.strip().lower() in self.tool_config.router_sentinel_replies

    def _emit_event(self, event_callback: Optional[Callable[[dict], None]], event: dict) -> None:
        if event_callback:
            event_callback(event)

    def _update_memory(
        self,
        user_input: str,
        assistant_reply: str,
        event_callback: Optional[Callable[[dict], None]] = None,
    ) -> None:
        clean_text = markdown_to_plain(assistant_reply)
        self.memory.set_event_callback(event_callback)
        try:
            self.memory.add_user_message(user_input)
            self.memory.add_assistant_message(clean_text)
            self.memory.save()
        finally:
            self.memory.set_event_callback(None)
