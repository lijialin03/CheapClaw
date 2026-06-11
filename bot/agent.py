# bot/agent.py
from typing import Any, Callable, Optional

from .assembler import Assembler
from .memory import Memory
from utils.processor import markdown_to_plain


class Agent:
    """轻量对话编排层，集中管理一轮对话、记忆压缩和客户端清理。"""

    def __init__(self, client: Any, assembler: Assembler, memory: Memory):
        self.client = client
        self.assembler = assembler
        self.memory = memory
        self.logger = getattr(client, "logger", None)

    def start(self) -> None:
        start = getattr(self.client, "start", None)
        if callable(start):
            start()

    def run_turn(self, user_input: str, event_callback: Optional[Callable[[dict], None]] = None) -> str:
        """执行一轮对话，并将清洗后的回复写入记忆。"""
        prompt = self.assembler.assemble(user_input)
        assistant_reply = self.client.send_text(prompt)
        clean_text = markdown_to_plain(assistant_reply)
        self.memory.set_event_callback(event_callback)
        try:
            self.assembler.update_memory(user_input, clean_text)
        finally:
            self.memory.set_event_callback(None)
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

    def close(self) -> None:
        """若底层客户端支持 close，则释放相关资源。"""
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
