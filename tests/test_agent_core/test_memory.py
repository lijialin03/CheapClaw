import json

from agent_core.config import MemoryConfig
from agent_core.memory import (
    ConversationBuffer,
    KeyInfoStore,
    Memory,
    MemoryCompressor,
    Message,
    estimate_tokens,
    extract_memory_tokens,
    is_generic_short_query,
    tokenize_memory_text,
)


def test_memory_text_helpers_are_stable_and_relative():
    assert estimate_tokens("") == 0
    assert estimate_tokens("你好") > estimate_tokens("hi")

    tokens = tokenize_memory_text("CheapClaw 读取配置文件")
    assert "cheapclaw" in tokens
    assert "配置文件" in tokens or "配置" in tokens
    assert extract_memory_tokens("CheapClaw CheapClaw") == {"cheapclaw"}
    assert is_generic_short_query("继续", ("继续",))
    assert is_generic_short_query("ok", ("继续",))
    assert not is_generic_short_query("读取 agent_core 文件", ("继续",))


def test_message_round_trip_and_conversation_buffer_context():
    message = Message("user", "hello", 3, 1.5)
    assert Message.from_dict(message.to_dict()) == message
    assert Message.from_dict({"role": "assistant", "content": "hi"}).token_count > 0

    buffer = ConversationBuffer(max_tokens=100)
    buffer.add("user", "first")
    buffer.add("assistant", "second")

    assert buffer.message_count() == 2
    assert buffer.total_tokens() > 0
    assert "用户: first" in buffer.get_context()
    assert "AI: second" in buffer.get_context()
    removed = buffer.pop_oldest_messages(keep=1)
    assert [item.content for item in removed] == ["first"]
    assert buffer.message_count() == 1


def test_memory_compressor_and_key_info_store():
    compressor = MemoryCompressor(max_summaries=2)
    assert (
        compressor.compress("old text", lambda prompt: " summary one ") == "summary one"
    )
    compressor.compress("old text", lambda prompt: "summary two")
    compressor.compress("old text", lambda prompt: "summary three")
    assert len(compressor.summaries) == 2
    assert "【历史摘要】" in compressor.get_context()
    assert (
        compressor.compress(
            "old text", lambda prompt: (_ for _ in ()).throw(RuntimeError())
        )
        == ""
    )

    store = KeyInfoStore()
    store.set("language", "python")
    store.bulk_set({"runner": "pytest"})
    assert store.get("language") == "python"
    assert store.get("missing", "fallback") == "fallback"
    assert "language: python" in store.get_context()
    snapshot = store.to_dict()
    store.clear()
    store.from_dict(snapshot)
    assert store.to_dict() == snapshot


def test_memory_selects_relevant_context_and_skips_generic_queries(tmp_path):
    memory = Memory(
        persist_path=tmp_path / "memory.json",
        config=MemoryConfig(context_budget=500, recent_context_budget=200),
    )
    memory.set_key_info("python", "Use pytest for CheapClaw tests")
    memory.set_key_info("frontend", "Use vite")
    memory.compressor.summaries.append(
        {"content": "pytest covered memory behavior", "timestamp": 1}
    )
    memory.compressor.summaries.append(
        {"content": "browser login state", "timestamp": 2}
    )
    memory.add_user_message("hello")
    memory.add_assistant_message("hi")

    context = memory.get_context("pytest memory")

    assert "Use pytest" in context
    assert "pytest covered memory" in context
    assert "【最近对话】" in context

    generic_context = memory.get_context("继续")
    assert "【最近对话】" in generic_context
    assert "【关键信息】" not in generic_context
    assert "【相关历史摘要】" not in generic_context


def test_memory_auto_compress_records_events_with_fake_llm():
    events = []
    memory = Memory(
        llm_call=lambda prompt: "compressed summary",
        buffer_max_tokens=20,
        config=MemoryConfig(
            buffer_max_tokens=20,
            auto_compress_threshold=0.1,
            auto_trim_threshold=0.9,
            min_messages_to_compress=4,
            recent_messages_to_keep=2,
        ),
    )
    memory.set_event_callback(events.append)

    for index in range(2):
        memory.add_user_message(f"user message {index} with extra words")
        memory.add_assistant_message(f"assistant message {index} with extra words")

    assert memory.compressor.summaries
    assert memory.buffer.message_count() <= 2
    assert {event["type"] for event in events} >= {
        "auto_compressing",
        "auto_compressed",
    }


def test_memory_auto_trim_records_events_without_llm():
    memory = Memory(
        buffer_max_tokens=20,
        config=MemoryConfig(
            buffer_max_tokens=20,
            auto_compress_threshold=0.1,
            auto_trim_threshold=0.2,
            min_messages_to_compress=4,
            recent_messages_to_keep=2,
        ),
    )

    for index in range(2):
        memory.add_user_message(f"user message {index} with extra words")
        memory.add_assistant_message(f"assistant message {index} with extra words")

    events = memory.consume_events()
    assert memory.buffer.message_count() <= 2
    assert {event["type"] for event in events} >= {"auto_trimming", "auto_trimmed"}


def test_memory_compress_with_summary_model():
    class SummaryModel:
        def __init__(self):
            self.prompts = []

        def send_text(self, prompt):
            self.prompts.append(prompt)
            return "model summary"

    memory = Memory(
        config=MemoryConfig(min_messages_to_compress=4, recent_messages_to_keep=2)
    )
    for index in range(2):
        memory.add_user_message(f"u{index}")
        memory.add_assistant_message(f"a{index}")

    model = SummaryModel()
    assert memory.compress_with_summary(model)
    assert model.prompts
    assert memory.compressor.summaries[-1]["content"] == "model summary"
    assert memory.buffer.message_count() == 2


def test_memory_save_load_clear_and_close_session_archive(tmp_path):
    persist_path = tmp_path / "memory.json"
    memory = Memory(
        persist_path=persist_path, llm_call=lambda prompt: "session summary"
    )
    memory.set_key_info("tool", "pytest")
    memory.add_user_message("hello")
    memory.add_assistant_message("world")
    memory.save()

    loaded = Memory(persist_path=persist_path)
    assert loaded.get_key_info("tool") == "pytest"
    assert loaded.buffer.message_count() == 0

    memory.add_user_message("again")
    result = memory.clear_session()
    assert result == {"cleared": 3, "remaining": 0}

    for index in range(2):
        memory.add_user_message(f"u{index}")
        memory.add_assistant_message(f"a{index}")
    closed = memory.close_session(compress=True)

    assert closed["message_count"] == 4
    assert closed["compressed"] is True
    assert closed["summary_count"] == 1
    archive_path = tmp_path / closed["path"].split("/")[-1]
    assert archive_path.exists()
    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    assert archive["messages"]
    assert archive["summaries"][0]["content"] == "session summary"
    assert memory.buffer.message_count() == 0
    assert memory.stats()["last_session"]["id"] == closed["id"]
