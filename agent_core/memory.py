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
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .prompt_loader import render_prompt


MEMORY_CONTEXT_BUDGET = 2500
KEY_INFO_CONTEXT_BUDGET = 600
SUMMARY_CONTEXT_BUDGET = 900
RECENT_CONTEXT_BUDGET = 1000
MAX_SELECTED_KEY_INFO = 8
MAX_SELECTED_SUMMARIES = 3
AUTO_COMPRESS_THRESHOLD = 0.8
AUTO_TRIM_THRESHOLD = 0.9
MIN_MESSAGES_TO_COMPRESS = 4
RECENT_MESSAGES_TO_KEEP = 2
SUMMARY_MESSAGE_CHAR_LIMIT = 600
GENERIC_SHORT_QUERIES = {"继续", "接着", "然后", "好的", "ok", "yes", "嗯", "好"}
BM25_K1 = 1.2
BM25_B = 0.75


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


def tokenize_memory_text(text: str) -> list[str]:
    """提取检索 token，英文保留词项，中文/混合文本补充字符 n-gram。"""
    if not text:
        return []

    lowered = text.lower()
    tokens = re.findall(r"[a-z0-9_][a-z0-9_.-]*", lowered)
    for cjk_run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        if len(cjk_run) <= 4:
            tokens.append(cjk_run)
        for size in (2, 3, 4):
            tokens.extend(cjk_run[index:index + size] for index in range(len(cjk_run) - size + 1))
    return [token for token in tokens if token]


def extract_memory_tokens(text: str) -> set[str]:
    return set(tokenize_memory_text(text))


def is_generic_short_query(query: str) -> bool:
    normalized = query.strip().lower()
    if not normalized:
        return False
    return normalized in GENERIC_SHORT_QUERIES or len(extract_memory_tokens(normalized)) <= 1


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
        self.persist_path = Path(persist_path) if persist_path else None
        self.last_session: dict = {}
        self._llm_available = llm_call is not None
        self._llm_call = llm_call or (lambda _: "")
        self.last_events: list[dict] = []
        self._event_callback: Optional[Callable[[dict], None]] = None

        if self.persist_path and self.persist_path.exists():
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

    def get_context(self, query: str = "", as_text: bool = True, budget: int = MEMORY_CONTEXT_BUDGET) -> str:
        """按当前输入筛选并组装分层记忆文本。"""
        return self._build_context(query=query, budget=budget)

    def compress_with_summary(self, summary_model=None) -> bool:
        """手动触发 LLM 摘要压缩。"""
        if summary_model is None:
            return self._try_compress()

        old_call = self._llm_call
        old_available = self._llm_available
        self._llm_call = lambda prompt: summary_model.send_text(prompt)
        self._llm_available = True
        try:
            return self._try_compress()
        finally:
            self._llm_call = old_call
            self._llm_available = old_available

    def clear(self):
        self.buffer.messages.clear()
        self.compressor.summaries.clear()
        self.key_info.clear()

    def close_session(self, compress: bool = True) -> dict:
        """结束当前进程内 session，必要时摘要后清空工作记忆。"""
        before_messages = self.buffer.message_count()
        before_summaries = len(self.compressor.summaries)
        compressed = False

        if compress and before_messages >= MIN_MESSAGES_TO_COMPRESS:
            compressed = self._try_compress(keep=0)

        self.buffer.messages.clear()
        self.last_session = {
            "closed_at": time.time(),
            "message_count": before_messages,
            "compressed": compressed,
            "summary_count": len(self.compressor.summaries) - before_summaries,
        }
        self.save()
        self._record_event({
            "type": "session_closed",
            "messages": before_messages,
            "compressed": compressed,
            "summaries_added": len(self.compressor.summaries) - before_summaries,
        })
        return dict(self.last_session)

    def clear_session(self) -> dict:
        count = self.buffer.message_count()
        self.buffer.messages.clear()
        self.save()
        return {"cleared": count, "remaining": 0}

    def stats(self) -> dict:
        return {
            "path": str(self.persist_path) if self.persist_path else "",
            "session_messages": self.buffer.message_count(),
            "session_tokens": self.buffer.total_tokens(),
            "summary_count": len(self.compressor.summaries),
            "key_info_count": len(self.key_info.to_dict()),
            "last_session": getattr(self, "last_session", {}),
        }

    def preview_context(self, query: str = "", budget: int = MEMORY_CONTEXT_BUDGET) -> dict:
        selected = self._select_context_items(query=query, budget=budget)
        return {
            "query": query,
            "budget": budget,
            "selected_key_info": len(selected["key_info"]),
            "selected_summaries": len(selected["summaries"]),
            "recent_messages": selected["recent_messages"],
            "key_info_tokens": selected["key_info_tokens"],
            "summary_tokens": selected["summary_tokens"],
            "recent_tokens": selected["recent_tokens"],
            "total_tokens": selected["total_tokens"],
        }

    # ---- Context 组装 -----------------------------------------------------

    def _build_context(self, query: str = "", budget: int = MEMORY_CONTEXT_BUDGET) -> str:
        selected = self._select_context_items(query=query, budget=budget)
        parts: list[str] = []

        if selected["key_info"]:
            lines = ["【关键信息】"]
            lines.extend(f"  {key}: {value}" for key, value in selected["key_info"])
            parts.append("\n".join(lines))

        if selected["summaries"]:
            lines = ["【相关历史摘要】"]
            lines.extend(f"  {index}. {summary['content']}" for index, summary in enumerate(selected["summaries"], 1))
            parts.append("\n".join(lines))

        if selected["recent_context"]:
            parts.append("【最近对话】\n" + selected["recent_context"])

        return "\n\n".join(parts)

    def _select_context_items(self, query: str = "", budget: int = MEMORY_CONTEXT_BUDGET) -> dict:
        query = query or ""
        generic_query = is_generic_short_query(query)
        recent_budget = min(RECENT_CONTEXT_BUDGET, budget)
        recent_context = self.buffer.get_context(budget=recent_budget)
        recent_tokens = estimate_tokens(recent_context)
        remaining_budget = max(0, budget - recent_tokens)

        key_budget = min(KEY_INFO_CONTEXT_BUDGET, remaining_budget)
        selected_key_info, key_tokens = self._select_key_info(query, key_budget, generic_query)
        remaining_budget = max(0, remaining_budget - key_tokens)

        summary_budget = min(SUMMARY_CONTEXT_BUDGET, remaining_budget)
        selected_summaries, summary_tokens = self._select_summaries(query, summary_budget, generic_query)

        return {
            "key_info": selected_key_info,
            "summaries": selected_summaries,
            "recent_context": recent_context,
            "recent_messages": self._count_recent_messages(recent_context),
            "key_info_tokens": key_tokens,
            "summary_tokens": summary_tokens,
            "recent_tokens": recent_tokens,
            "total_tokens": key_tokens + summary_tokens + recent_tokens,
        }

    def _select_key_info(self, query: str, budget: int, generic_query: bool) -> tuple[list[tuple[str, str]], int]:
        if budget <= 0 or not self.key_info.to_dict() or generic_query:
            return [], 0
        scored = []
        items = list(self.key_info.to_dict().items())
        corpus_tokens = [tokenize_memory_text(f"{key}: {value}") for key, value in items]
        for index, (key, value) in enumerate(items):
            text = f"{key}: {value}"
            score = self._score_memory_text(query, text, corpus_tokens)
            if query and score <= 0:
                continue
            scored.append((score + index * 0.001, key, value, estimate_tokens(text)))
        scored.sort(reverse=True, key=lambda item: item[0])
        selected: list[tuple[str, str]] = []
        total = 0
        for _, key, value, tokens in scored:
            if len(selected) >= MAX_SELECTED_KEY_INFO or total + tokens > budget:
                continue
            selected.append((key, value))
            total += tokens
        return selected, total

    def _select_summaries(self, query: str, budget: int, generic_query: bool) -> tuple[list[dict], int]:
        if budget <= 0 or not self.compressor.summaries or generic_query:
            return [], 0
        scored = []
        corpus_tokens = [tokenize_memory_text(summary.get("content", "")) for summary in self.compressor.summaries]
        for index, summary in enumerate(self.compressor.summaries):
            content = summary.get("content", "")
            score = self._score_memory_text(query, content, corpus_tokens)
            if query and score <= 0:
                continue
            scored.append((score + index * 0.01, summary, estimate_tokens(content)))
        scored.sort(reverse=True, key=lambda item: item[0])
        selected: list[dict] = []
        total = 0
        for _, summary, tokens in scored:
            if len(selected) >= MAX_SELECTED_SUMMARIES or total + tokens > budget:
                continue
            selected.append(summary)
            total += tokens
        selected.sort(key=lambda item: item.get("timestamp", 0))
        return selected, total

    def _score_memory_text(self, query: str, text: str, corpus_tokens: list[list[str]] = None) -> float:
        if not query:
            return 0.1
        if corpus_tokens is None:
            corpus_tokens = [tokenize_memory_text(text)]
        query_tokens = tokenize_memory_text(query)
        document_tokens = tokenize_memory_text(text)
        if not query_tokens or not document_tokens:
            return 0.0
        return self._bm25_score(query_tokens, document_tokens, corpus_tokens)

    def _bm25_score(self, query_tokens: list[str], document_tokens: list[str], corpus_tokens: list[list[str]]) -> float:
        document_count = len(corpus_tokens)
        if document_count == 0:
            return 0.0

        avg_document_length = sum(len(tokens) for tokens in corpus_tokens) / document_count or 1
        document_length = len(document_tokens) or 1
        term_frequencies = self._term_counts(document_tokens)
        document_frequencies = self._document_frequencies(corpus_tokens)
        score = 0.0

        for token in set(query_tokens):
            frequency = term_frequencies.get(token, 0)
            if frequency == 0:
                continue
            containing_documents = document_frequencies.get(token, 0)
            idf = math.log(1 + (document_count - containing_documents + 0.5) / (containing_documents + 0.5))
            denominator = frequency + BM25_K1 * (1 - BM25_B + BM25_B * document_length / avg_document_length)
            score += idf * frequency * (BM25_K1 + 1) / denominator
        return score

    def _term_counts(self, tokens: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        return counts

    def _document_frequencies(self, corpus_tokens: list[list[str]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for tokens in corpus_tokens:
            for token in set(tokens):
                counts[token] = counts.get(token, 0) + 1
        return counts

    def _count_recent_messages(self, recent_context: str) -> int:
        if not recent_context:
            return 0
        return sum(1 for line in recent_context.splitlines() if line.startswith(("用户:", "AI:")))

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

    def _try_compress(self, keep: int = RECENT_MESSAGES_TO_KEEP) -> bool:
        """将工作记忆中较早消息压缩为摘要（需 LLM 支持）。"""
        if not self._llm_available or len(self.buffer.messages) < MIN_MESSAGES_TO_COMPRESS:
            return False
        removed = self.buffer.messages[:-keep] if keep > 0 else self.buffer.messages[:]
        if not removed:
            return False

        text = self._format_messages_for_summary(removed)
        summary = self.compressor.compress(text, self._llm_call)
        if not summary:
            return False
        self.buffer.messages = self.buffer.messages[-keep:] if keep > 0 else []
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
            "summaries": self.compressor.to_dict(),
            "key_info": self.key_info.to_dict(),
            "last_session": self.last_session,
        }
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.persist_path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    def load(self):
        if not self.persist_path or not self.persist_path.exists():
            return
        with open(self.persist_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        self.buffer.messages.clear()
        self.compressor.from_dict(data.get("summaries", []))
        self.key_info.from_dict(data.get("key_info", {}))
        self.last_session = data.get("last_session", {})
