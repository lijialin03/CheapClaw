# bot/agent.py
import difflib
import json
import re
import shlex
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

from .assembler import Assembler
from .memory import Memory
from .workspace import WorkspaceEditPlan, WorkspaceError
from utils.processor import markdown_to_plain


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Agent:
    """轻量对话编排层，集中管理一轮对话、记忆压缩和客户端清理。"""

    def __init__(
        self,
        client: Any,
        assembler: Assembler,
        memory: Memory,
        workspace: Any = None,
        max_text_chars: int = 3500,
        upload_dir: str | Path = None,
        file_transport_enabled: bool = True,
    ):
        self.client = client
        self.assembler = assembler
        self.memory = memory
        self.workspace = workspace
        self.max_text_chars = max_text_chars
        self.upload_dir = Path(upload_dir) if upload_dir else PROJECT_ROOT / ".cheapclaw" / "uploads"
        self.file_transport_enabled = file_transport_enabled
        self.logger = getattr(client, "logger", None)
        self._staged_edit = None

    def start(self) -> None:
        start = getattr(self.client, "start", None)
        if callable(start):
            start()

    def run_turn(self, user_input: str, event_callback: Optional[Callable[[dict], None]] = None) -> str:
        """执行一轮对话，并将清洗后的回复写入记忆。"""
        prompt = self.assembler.assemble(user_input)
        assistant_reply = self._send_prompt(prompt, event_callback)
        self._update_memory(user_input, assistant_reply, event_callback)
        return assistant_reply

    def _send_prompt(self, prompt: str, event_callback: Optional[Callable[[dict], None]] = None) -> str:
        if self.file_transport_enabled and len(prompt) > self.max_text_chars:
            self._emit_event(event_callback, {"type": "file_transporting"})
            prompt_file = self._write_upload_prompt(prompt)
            return self.client.send_file(
                str(prompt_file),
                prompt="请根据上传文件中的完整上下文回答用户最新问题。",
            )
        return self.client.send_text(prompt)

    def _emit_event(self, event_callback: Optional[Callable[[dict], None]], event: dict) -> None:
        if event_callback:
            event_callback(event)

    def _write_upload_prompt(self, prompt: str) -> Path:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = self.upload_dir / f"prompt-{uuid4().hex}.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        return prompt_file

    def _update_memory(
        self,
        user_input: str,
        assistant_reply: str,
        event_callback: Optional[Callable[[dict], None]] = None,
    ) -> None:
        clean_text = markdown_to_plain(assistant_reply)
        self.memory.set_event_callback(event_callback)
        try:
            self.assembler.update_memory(user_input, clean_text)
        finally:
            self.memory.set_event_callback(None)

    def read_file(self, path: str, start_line: int = None, limit: int = None) -> dict:
        workspace = self._require_workspace()
        content = workspace.read_text(path, start_line=start_line, limit=limit)
        stat = workspace.stat(path)
        return {"type": "file", "path": stat["path"], "content": content, "stat": stat}

    def ask_file(
        self,
        path: str,
        question: str,
        event_callback: Optional[Callable[[dict], None]] = None,
    ) -> str:
        workspace = self._require_workspace()
        resolved_path = workspace.assert_editable_text_file(path)
        self._emit_event(event_callback, {"type": "file_transporting"})
        assistant_reply = self.client.send_file(str(resolved_path), prompt=question)
        memory_user_input = f"用户询问文件 {workspace.stat(path)['path']}：{question}"
        self._update_memory(memory_user_input, assistant_reply, event_callback)
        return assistant_reply

    def handle_command(self, raw_input: str, event_callback: Optional[Callable[[dict], None]] = None) -> dict:
        try:
            parts = shlex.split(raw_input)
        except ValueError as exc:
            return {"type": "error", "message": f"命令解析失败: {exc}"}
        if not parts:
            return {"type": "error", "message": "空命令"}

        command = parts[0].lower()
        try:
            if command == "/ls":
                path = parts[1] if len(parts) > 1 else "."
                entries = self._require_workspace().list_dir(path)
                return {"type": "list", "path": path, "entries": entries}
            if command == "/read":
                if len(parts) < 2:
                    return {"type": "error", "message": "用法: /read path [start_line] [limit]"}
                start_line = int(parts[2]) if len(parts) > 2 else None
                limit = int(parts[3]) if len(parts) > 3 else None
                return self.read_file(parts[1], start_line=start_line, limit=limit)
            if command == "/ask-file":
                if len(parts) < 3:
                    return {"type": "error", "message": "用法: /ask-file path question"}
                question = " ".join(parts[2:])
                reply = self.ask_file(parts[1], question, event_callback)
                return {"type": "assistant", "content": reply}
            if command == "/edit":
                if len(parts) < 3:
                    return {"type": "error", "message": "用法: /edit path instruction"}
                instruction = " ".join(parts[2:])
                return self.stage_edit(parts[1], instruction, event_callback)
            if command == "/diff":
                return self.current_diff()
            if command == "/apply":
                return self.apply_staged_edit()
            if command == "/discard":
                return self.discard_staged_edit()
            return {"type": "error", "message": f"未知命令: {command}"}
        except (WorkspaceError, FileNotFoundError, ValueError) as exc:
            return {"type": "error", "message": str(exc)}

    def stage_edit(
        self,
        path: str,
        instruction: str,
        event_callback: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        workspace = self._require_workspace()
        resolved_path = workspace.assert_editable_text_file(path)
        relative_path = workspace.stat(path)["path"]
        content = resolved_path.read_text(encoding="utf-8")
        prompt = (
            "你将为一个本地文本文件生成可安全预览的编辑计划。\n"
            "只返回一个 JSON 对象，不要使用 Markdown 代码块，不要添加额外解释。\n"
            "JSON 字段必须是 path、old_text、new_text、reason。\n"
            "old_text 必须是文件中需要替换的一段原文，且应尽量短但足以唯一匹配。\n"
            "new_text 是替换后的文本。不要删除文件，不要返回统一 diff。\n\n"
            f"文件路径: {relative_path}\n"
            f"编辑需求: {instruction}\n\n"
            "文件内容如下:\n"
            f"{content}"
        )
        reply = self._send_prompt(prompt, event_callback)
        plan = self._parse_edit_plan(reply, relative_path)
        workspace.assert_editable_text_file(plan.path)
        self._staged_edit = plan
        return {"type": "edit_plan", "plan": self._plan_to_dict(plan), "diff": self._diff_for_plan(plan)}

    def current_diff(self) -> dict:
        if not self._staged_edit:
            return {"type": "error", "message": "当前没有 staged edit"}
        return {"type": "edit_plan", "plan": self._plan_to_dict(self._staged_edit), "diff": self._diff_for_plan(self._staged_edit)}

    def apply_staged_edit(self) -> dict:
        if not self._staged_edit:
            return {"type": "error", "message": "当前没有 staged edit"}
        self._require_workspace().apply_edit_plan(self._staged_edit)
        applied_path = self._staged_edit.path
        self._staged_edit = None
        return {"type": "text", "content": f"已应用 staged edit: {applied_path}"}

    def discard_staged_edit(self) -> dict:
        if not self._staged_edit:
            return {"type": "text", "content": "当前没有 staged edit"}
        discarded_path = self._staged_edit.path
        self._staged_edit = None
        return {"type": "text", "content": f"已丢弃 staged edit: {discarded_path}"}

    def _parse_edit_plan(self, reply: str, expected_path: str) -> WorkspaceEditPlan:
        match = re.search(r"\{.*\}", reply, re.DOTALL)
        if not match:
            raise WorkspaceError("未能从模型回复中解析编辑计划 JSON")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise WorkspaceError(f"编辑计划 JSON 解析失败: {exc}") from exc

        missing = [field for field in ("old_text", "new_text", "reason") if field not in data]
        if missing:
            raise WorkspaceError(f"编辑计划缺少字段: {', '.join(missing)}")
        return WorkspaceEditPlan(
            path=expected_path,
            old_text=data["old_text"],
            new_text=data["new_text"],
            reason=data["reason"],
        )

    def _diff_for_plan(self, plan: WorkspaceEditPlan) -> str:
        diff_lines = difflib.unified_diff(
            plan.old_text.splitlines(),
            plan.new_text.splitlines(),
            fromfile=f"a/{plan.path}",
            tofile=f"b/{plan.path}",
            lineterm="",
        )
        return "\n".join(diff_lines)

    def _plan_to_dict(self, plan: WorkspaceEditPlan) -> dict:
        return {
            "path": plan.path,
            "old_text": plan.old_text,
            "new_text": plan.new_text,
            "reason": plan.reason,
        }

    def _require_workspace(self):
        if self.workspace is None:
            raise WorkspaceError("未配置 workspace")
        return self.workspace

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

    def close(self) -> None:
        """若底层客户端支持 close，则释放相关资源。"""
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
