# bot/memory.py
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


# ---------------------------------------------------------------------------
# Token 估算（不依赖外部 tokenizer）
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """估算文本的 token 数（近似值，不引入外部依赖）。

    中文字符约 1.5 token/字，英文及其他字符约 0.3 token/字符。
    """
    if not text:
        return 0
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
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
        return {"role": self.role, "content": self.content,
                "token_count": self.token_count, "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(role=d["role"], content=d["content"],
                   token_count=d.get("token_count", estimate_tokens(d["content"])),
                   timestamp=d.get("timestamp", 0.0))


class ConversationBuffer:
    """Level 1: 工作记忆

    保留最近的完整对话，超过 ``max_tokens`` 时触发压缩通知。
    """

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
        return sum(m.token_count for m in self.messages)

    def message_count(self) -> int:
        return len(self.messages)

    def pop_oldest_pairs(self, keep: int = 2) -> list[Message]:
        """弹出最旧的 ``len(messages) - keep`` 条消息，返回被弹出的消息。"""
        if len(self.messages) <= keep:
            return []
        removed = self.messages[:-keep]
        self.messages = self.messages[-keep:]
        return removed

    def get_context(self, budget: int = None) -> str:
        """在 ``budget`` token 内返回最近的对话文本。"""
        budget = budget or self.max_tokens
        selected: list[Message] = []
        total = 0
        for msg in reversed(self.messages):
            if total + msg.token_count > budget:
                break
            selected.append(msg)
            total += msg.token_count
        selected.reverse()
        lines = []
        for msg in selected:
            role = "用户" if msg.role == "user" else "AI"
            lines.append(f"{role}: {msg.content}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Level 2: 压缩记忆
# ---------------------------------------------------------------------------

class MemoryCompressor:
    """Level 2: 压缩记忆

    将老的历史对话通过 LLM 压缩为摘要，保存在 ``summaries`` 列表中。
    ``max_summaries`` 限制摘要数量，超出时自动合并最早的条目。
    """

    def __init__(self, max_summaries: int = 5):
        self.summaries: list[dict] = []   # [{"content": str, "timestamp": float}, ...]
        self.max_summaries = max_summaries

    def compress(self, text: str, llm_call: Callable[[str], str]) -> str:
        """调用 LLM 对 ``text`` 做摘要，存入 summaries 列表。

        Args:
            text: 需要压缩的对话文本。
            llm_call: 接收 prompt 返回 summary 的可调用对象。

        Returns:
            生成的摘要文本；若 LLM 调用失败返回空字符串。
        """
        prompt = (
            "请用一两句话概括以下对话的核心内容，"
            "保留关键事实、用户偏好、决策、讨论的技术方案：\n"
            f"{text}"
        )
        try:
            summary = llm_call(prompt)
            summary = summary.strip()
            if summary:
                self.summaries.append({
                    "content": summary,
                    "timestamp": time.time(),
                })
                self._trim()
            return summary
        except Exception:
            return ""

    def _trim(self):
        """超出 ``max_summaries`` 时，将最早的两条合并为一条。"""
        while len(self.summaries) > self.max_summaries:
            merged = self.summaries.pop(0)
            merged["content"] += "\n" + self.summaries.pop(0)["content"]
            self.summaries.insert(0, merged)

    def get_context(self) -> str:
        if not self.summaries:
            return ""
        lines = ["【历史摘要】"]
        for i, s in enumerate(self.summaries, 1):
            lines.append(f"  {i}. {s['content']}")
        return "\n".join(lines)

    def to_dict(self) -> list:
        return self.summaries

    def from_dict(self, data: list):
        self.summaries = data[:]


# ---------------------------------------------------------------------------
# Level 3: 关键信息（简化版 key-value store）
# ---------------------------------------------------------------------------

class KeyInfoStore:
    """Level 3: 关键信息

    存储从对话中提取的持久化信息（用户偏好、技术决策、项目约定等）。
    当前为手动 set/get 模式；可扩展为自动抽取。
    """

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
        for k, v in self._infos.items():
            lines.append(f"  {k}: {v}")
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
    """分层对话记忆管理器

    用法:
        >>> memory = Memory(
        ...     buffer_max_tokens=3000,
        ...     max_summaries=5,
        ...     persist_path="memory.json",
        ...     llm_call=client.send_text,   # 用于自动压缩
        ... )
        >>> memory.add_user_message("你好")
        >>> memory.add_assistant_message("你好！有什么可以帮你的？")
        >>> print(memory.get_context())

    自动管理:
        - 每次添加 assistant 消息后检查工作记忆是否超过阈值
        - 超过时将最早的一批消息压缩为摘要放入 Level 2
    """

    def __init__(
        self,
        persist_path: Optional[str] = None,
        llm_call: Optional[Callable[[str], str]] = None,
        buffer_max_tokens: int = 3000,
        max_summaries: int = 5,
    ):
        """
        Args:
            persist_path: 持久化 JSON 文件路径。
            llm_call: 调用 LLM 生成摘要的函数（接收 prompt 返回文本）。
            buffer_max_tokens: 工作记忆 token 上限。
            max_summaries: 最多保留的摘要条数。
        """
        self.buffer = ConversationBuffer(max_tokens=buffer_max_tokens)
        self.compressor = MemoryCompressor(max_summaries=max_summaries)
        self.key_info = KeyInfoStore()
        self.persist_path = persist_path
        self._llm_available = llm_call is not None
        self._llm_call = llm_call or (lambda _: "")

        if persist_path and os.path.exists(persist_path):
            self.load()

    # ---- 公开 API ---------------------------------------------------------

    def add_user_message(self, content: str):
        self.buffer.add("user", content)

    def add_assistant_message(self, content: str):
        self.buffer.add("assistant", content)
        # 回复写完后检查是否需要压缩（避免在 get_context 时才压缩）
        self._maybe_compress()

    def set_key_info(self, key: str, value: str):
        self.key_info.set(key, value)

    def get_key_info(self, key: str, default: str = None) -> Optional[str]:
        return self.key_info.get(key, default)

    def get_context(self, as_text: bool = True) -> str:
        """组装分层记忆文本。

        按优先级: 关键信息 > 历史摘要 > 最近对话。
        ``as_text`` 参数保留以便向后兼容。
        """
        parts: list[str] = []

        # Level 3
        kic = self.key_info.get_context()
        if kic:
            parts.append(kic)

        # Level 2
        sc = self.compressor.get_context()
        if sc:
            parts.append(sc)

        # Level 1（预留 2000 tokens）
        rc = self.buffer.get_context(budget=2000)
        if rc:
            parts.append("【最近对话】\n" + rc)

        return "\n\n".join(parts)

    def compress_with_summary(self, summary_model=None) -> None:
        """手动触发 LLM 摘要压缩。

        调用 LLM 对工作记忆中最旧的消息做摘要，存入 Level 2。
        ``summary_model`` 可以传入外部 LLM 客户端（须有 ``send_text`` 方法）；
        若为 None，则使用初始化时传入的 ``llm_call``。
        """
        if summary_model is not None:
            old_call = self._llm_call
            self._llm_call = lambda p: summary_model.send_text(p)
            self._try_compress()
            self._llm_call = old_call
        else:
            self._try_compress()

    def clear(self):
        self.buffer.messages.clear()
        self.compressor.summaries.clear()
        self.key_info.clear()

    # ---- 压缩逻辑 --------------------------------------------------------

    def _maybe_compress(self):
        """工作记忆超过阈值时自动压缩。

        优先使用 LLM 将最早的消息对压缩为摘要（存入 Level 2）；
        若 LLM 不可用或压缩后仍超限，回退到直接丢弃最旧消息。
        """
        if self.buffer.total_tokens() < self.buffer.max_tokens * 0.8:
            return

        # 1) 尝试 LLM 摘要压缩（如果有 LLM 可用）
        if self._llm_available and self.buffer.message_count() >= 4:
            self._try_compress()

        # 2) 若仍超限，丢弃最旧消息对释放空间
        while (self.buffer.total_tokens() > self.buffer.max_tokens * 0.9
               and self.buffer.message_count() >= 4):
            self.buffer.pop_oldest_pairs(keep=self.buffer.message_count() - 2)

    def _try_compress(self) -> None:
        """将工作记忆中最旧的一批消息压缩为摘要（需 LLM 支持）。"""
        if len(self.buffer.messages) < 4:
            return
        removed = self.buffer.pop_oldest_pairs(keep=2)
        if not removed:
            return
        text = "\n".join(
            f"{'用户' if m.role == 'user' else 'AI'}: {m.content[:600]}"
            for m in removed
        )
        self.compressor.compress(text, self._llm_call)

    # ---- 持久化 ---------------------------------------------------------

    def save(self):
        if not self.persist_path:
            return
        data = {
            "buffer": [m.to_dict() for m in self.buffer.messages],
            "summaries": self.compressor.to_dict(),
            "key_info": self.key_info.to_dict(),
        }
        with open(self.persist_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self):
        if not self.persist_path or not os.path.exists(self.persist_path):
            return
        with open(self.persist_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.buffer.messages = [Message.from_dict(m) for m in data.get("buffer", [])]
        self.compressor.from_dict(data.get("summaries", []))
        self.key_info.from_dict(data.get("key_info", {}))