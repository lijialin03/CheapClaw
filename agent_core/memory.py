# agent_core/memory.py
"""
分层对话记忆系统

架构（三层）:
  Level 1 - 工作记忆 (ConversationBuffer)
    保留最近的完整对话，受 token 预算约束
  Level 2 - 压缩记忆 (MemoryCompressor)
    对更早的历史做 LLM 摘要，保留关键信息
  Level 3 - 关键信息 (KeyInfoStore)
    持久化的用户偏好/技术决策/事实等

组装策略（按优先级降序）:
  关键信息 → 历史摘要 → 最近对话
"""
import json
import os
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .prompt_loader import render_prompt


RECENT_CONTEXT_BUDGET = 2000
AUTO_COMPRESS_THRESHOLD = 0.8
AUTO_TRIM_THRESHOLD = 0.9
MIN_MESSAGES_TO_COMPRESS = 4
RECENT_MESSAGES_TO_KEEP = 2
SUMMARY_MESSAGE_CHAR_LIMIT = 600


# ---------------------------------------------------------------------------
# Token 估算（不依赖外部 tokenizer）
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """估算文本的 token 数（近似值，不引入外部依赖）。

    中文字符约 1.5 token/字，英文及其他字符约 0.3 token/字符。
    """
    if not text:
        return 0
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    other = len(text) - cjk
    return int(cjk * 1.5 + other * 0.3) + 2


# ---------------------------------------------------------------------------
# Level 1: 工作记忆
# ---------------------------------------------------------------------------

@dataclass
class Message:
    """单条对话消息"""

    role: str          # "user" | "assistant"
    content: str
    token_count: int
    timestamp: float

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "token_count": self.token_count,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        return cls(
            role=data["role"],
            content=data["content"],
            token_count=data.get("token_count", estimate_tokens(data["content"])),
            timestamp=data.get("timestamp", 0.0),
        )


class ConversationBuffer:
    """Level 1: 工作记忆，保留最近的完整对话。"""

    def __init__(self, max_tokens: int = 3000):
        self.messages: list[Message] = []
        self.max_tokens = max_tokens

    def add(self, role: str, content: str) -> None:
        self.messages.append(Message(
            role=role,
            content=content,
            token_count=estimate_tokens(content),
            timestamp=time.time(),
        ))

    def total_tokens(self) -> int:
        return sum(message.token_count for message in self.messages)

    def message_count(self) -> int:
        return len(self.messages)

    def pop_oldest_messages(self, keep: int = RECENT_MESSAGES_TO_KEEP) -> list[Message]:
        """弹出最旧的 ``len(messages) - keep`` 条消息，返回被弹出的消息。"""
        if len(self.messages) <= keep:
            return []
        removed = self.messages[:-keep]
        self.messages = self.messages[-keep:]
        return removed

    def pop_oldest_pairs(self, keep: int = RECENT_MESSAGES_TO_KEEP) -> list[Message]:
        """兼容旧命名；实际按消息条数保留。"""
        return self.pop_oldest_messages(keep=keep)

    def get_context(self, budget: int = None) -> str:
        """在 ``budget`` token 内返回最近的对话文本。"""
        budget = budget or self.max_tokens
        selected: list[Message] = []
        total = 0
        for message in reversed(self.messages):
            if total + message.token_count > budget:
                break
            selected.append(message)
            total += message.token_count
        selected.reverse()
        return "\n".join(self._format_message(message) for message in selected)

    def _format_message(self, message: Message) -> str:
        role = "用户" if message.role == "user" else "AI"
        return f"{role}: {message.content}"


# ---------------------------------------------------------------------------
# Level 2: 压缩记忆
# ---------------------------------------------------------------------------

class MemoryCompressor:
    """Level 2: 压缩记忆，将老的历史对话通过 LLM 压缩为摘要。"""

    def __init__(self, max_summaries: int = 5):
        self.summaries: list[dict] = []   # [{"content": str, "timestamp": float}, ...]
        self.max_summaries = max_summaries

    def compress(self, text: str, llm_call: Callable[[str], str]) -> str:
        """调用 LLM 对 ``text`` 做摘要，存入 summaries 列表。"""
        prompt = self._build_summary_prompt(text)
        try:
            summary = llm_call(prompt).strip()
            if summary:
                self.summaries.append({
                    "content": summary,
                    "timestamp": time.time(),
                })
                self._trim()
            return summary
        except Exception:
            return ""

    def get_context(self) -> str:
        if not self.summaries:
            return ""
        lines = ["【历史摘要】"]
        for index, summary in enumerate(self.summaries, 1):
            lines.append(f"  {index}. {summary['content']}")
        return "\n".join(lines)

    def to_dict(self) -> list:
        return self.summaries

    def from_dict(self, data: list):
        self.summaries = data[:]

    def _build_summary_prompt(self, text: str) -> str:
        return render_prompt("memory_summary.md", text=text)

    def _trim(self):
        """超出 ``max_summaries`` 时，将最早的两条合并为一条。"""
        while len(self.summaries) > self.max_summaries:
            merged = self.summaries.pop(0)
            merged["content"] += "\n" + self.summaries.pop(0)["content"]
            self.summaries.insert(0, merged)


# ---------------------------------------------------------------------------
# Level 3: 关键信息（简化版 key-value store）
# ---------------------------------------------------------------------------

class KeyInfoStore:
    """Level 3: 关键信息，存储持久化偏好、技术决策和项目约定。"""

    def __init__(self):
        self._infos: dict[str, str] = {}

    def set(self, key: str, value: str):
        self._infos[key] = value

    def get(self, key: str, default: str = None) -> Optional[str]:
        return self._infos.get(key, default)

    def bulk_set(self, pairs: dict[str, str]):
        self._infos.update(pairs)

    def get_context(self) -> str:
        if not self._infos:
            return ""
        lines = ["【关键信息】"]
        for key, value in self._infos.items():
            lines.append(f"  {key}: {value}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return dict(self._infos)

    def from_dict(self, data: dict):
        self._infos = dict(data)

    def clear(self):
        self._infos.clear()


# ---------------------------------------------------------------------------
# Memory — 统一入口
# ---------------------------------------------------------------------------

class Memory:
    """分层对话记忆管理器。"""

    def __init__(
        self,
        persist_path: Optional[str] = None,
        llm_call: Optional[Callable[[str], str]] = None,
        buffer_max_tokens: int = 3000,
        max_summaries: int = 5,
    ):
        self.buffer = ConversationBuffer(max_tokens=buffer_max_tokens)
        self.compressor = MemoryCompressor(max_summaries=max_summaries)
        self.key_info = KeyInfoStore()
        self.persist_path = persist_path
        self._llm_available = llm_call is not None
        self._llm_call = llm_call or (lambda _: "")
        self.last_events: list[dict] = []
        self._event_callback: Optional[Callable[[dict], None]] = None

        if persist_path and os.path.exists(persist_path):
            self.load()

    # ---- 公开 API ---------------------------------------------------------

    def add_user_message(self, content: str):
        self.buffer.add("user", content)

    def add_assistant_message(self, content: str):
        self.last_events.clear()
        self.buffer.add("assistant", content)
        self._maybe_compress()

    def consume_events(self) -> list[dict]:
        events = self.last_events[:]
        self.last_events.clear()
        return events

    def set_event_callback(self, callback: Optional[Callable[[dict], None]]) -> None:
        self._event_callback = callback

    def set_key_info(self, key: str, value: str):
        self.key_info.set(key, value)

    def get_key_info(self, key: str, default: str = None) -> Optional[str]:
        return self.key_info.get(key, default)

    def get_context(self, as_text: bool = True) -> str:
        """组装分层记忆文本。

        按优先级: 关键信息 > 历史摘要 > 最近对话。
        ``as_text`` 参数保留以便向后兼容。
        """
        return self._build_context()

    def compress_with_summary(self, summary_model=None) -> bool:
        """手动触发 LLM 摘要压缩。"""
        if summary_model is None:
            return self._try_compress()

        old_call = self._llm_call
        self._llm_call = lambda prompt: summary_model.send_text(prompt)
        try:
            return self._try_compress()
        finally:
            self._llm_call = old_call

    def clear(self):
        self.buffer.messages.clear()
        self.compressor.summaries.clear()
        self.key_info.clear()

    # ---- Context 组装 -----------------------------------------------------

    def _build_context(self) -> str:
        parts: list[str] = []
        self._append_context_part(parts, self.key_info.get_context())
        self._append_context_part(parts, self.compressor.get_context())

        recent_context = self.buffer.get_context(budget=RECENT_CONTEXT_BUDGET)
        if recent_context:
            parts.append("【最近对话】\n" + recent_context)

        return "\n\n".join(parts)

    def _append_context_part(self, parts: list[str], context: str) -> None:
        if context:
            parts.append(context)

    # ---- 压缩逻辑 --------------------------------------------------------

    def _maybe_compress(self):
        """工作记忆超过阈值时自动压缩。"""
        if self.buffer.total_tokens() < self.buffer.max_tokens * AUTO_COMPRESS_THRESHOLD:
            return

        if self._llm_available and self.buffer.message_count() >= MIN_MESSAGES_TO_COMPRESS:
            self._auto_compress()

        while (
            self.buffer.total_tokens() > self.buffer.max_tokens * AUTO_TRIM_THRESHOLD
            and self.buffer.message_count() >= MIN_MESSAGES_TO_COMPRESS
        ):
            self._trim_oldest_messages()

    def _auto_compress(self) -> None:
        before_summaries = len(self.compressor.summaries)
        before_messages = self.buffer.message_count()
        self._record_event({"type": "auto_compressing"})
        compressed = self._try_compress()
        if compressed and len(self.compressor.summaries) > before_summaries:
            self._record_event({
                "type": "auto_compressed",
                "removed": before_messages - self.buffer.message_count(),
                "summaries": len(self.compressor.summaries),
                "remaining": self.buffer.message_count(),
            })

    def _try_compress(self) -> bool:
        """将工作记忆中最旧的一批消息压缩为摘要（需 LLM 支持）。"""
        if len(self.buffer.messages) < MIN_MESSAGES_TO_COMPRESS:
            return False
        removed = self.buffer.messages[:-RECENT_MESSAGES_TO_KEEP]
        if not removed:
            return False

        text = self._format_messages_for_summary(removed)
        summary = self.compressor.compress(text, self._llm_call)
        if not summary:
            return False
        self.buffer.messages = self.buffer.messages[-RECENT_MESSAGES_TO_KEEP:]
        return True

    def _trim_oldest_messages(self) -> None:
        before_messages = self.buffer.message_count()
        self._record_event({"type": "auto_trimming"})
        self.buffer.pop_oldest_messages(keep=self.buffer.message_count() - 2)
        self._record_event({
            "type": "auto_trimmed",
            "removed": before_messages - self.buffer.message_count(),
            "remaining": self.buffer.message_count(),
        })

    def _format_messages_for_summary(self, messages: list[Message]) -> str:
        return "\n".join(
            f"{'用户' if message.role == 'user' else 'AI'}: {message.content[:SUMMARY_MESSAGE_CHAR_LIMIT]}"
            for message in messages
        )

    def _record_event(self, event: dict) -> None:
        self.last_events.append(event)
        if self._event_callback:
            self._event_callback(event)

    # ---- 持久化 ---------------------------------------------------------

    def save(self):
        if not self.persist_path:
            return
        data = {
            "buffer": [message.to_dict() for message in self.buffer.messages],
            "summaries": self.compressor.to_dict(),
            "key_info": self.key_info.to_dict(),
        }
        with open(self.persist_path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    def load(self):
        if not self.persist_path or not os.path.exists(self.persist_path):
            return
        with open(self.persist_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        self.buffer.messages = [Message.from_dict(message) for message in data.get("buffer", [])]
        self.compressor.from_dict(data.get("summaries", []))
        self.key_info.from_dict(data.get("key_info", {}))
