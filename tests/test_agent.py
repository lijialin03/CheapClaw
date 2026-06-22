from pathlib import Path

import pytest

from agent_core.agent import Agent
from agent_core.config import AgentConfig, ToolConfig


class MinimalTextClient:
    def __init__(self, reply="minimal reply"):
        self.reply = reply
        self.sent_texts = []

    def send_text(self, text, **kwargs):
        self.sent_texts.append(text)
        return self.reply


class MinimalFileClient(MinimalTextClient):
    def __init__(self, reply="file reply"):
        super().__init__(reply)
        self.sent_files = []

    def send_file(self, file_path, prompt=None):
        self.sent_files.append((file_path, prompt))
        return self.reply


class FakeClient:
    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.sent_texts = []
        self.sent_files = []
        self.started = 0
        self.closed = 0
        self.notices = [{"type": "notice", "message": "hi"}]

    def start(self):
        self.started += 1

    def close(self):
        self.closed += 1

    def send_text(self, text, **kwargs):
        self.sent_texts.append(text)
        return self.replies.pop(0) if self.replies else "client reply"

    def send_file(self, file_path, prompt=None):
        self.sent_files.append((file_path, prompt))
        return self.replies.pop(0) if self.replies else "file reply"

    def consume_notices(self):
        notices = self.notices[:]
        self.notices.clear()
        return notices


class FakeMemory:
    def __init__(self):
        self.context_queries = []
        self.user_messages = []
        self.assistant_messages = []
        self.saved = 0
        self.closed = 0
        self.callbacks = []
        self.events = [{"type": "memory"}]
        self.buffer = type("Buffer", (), {"messages": []})()
        self.compressor = type("Compressor", (), {"summaries": []})()
        self.clear_result = {"cleared": 1, "remaining": 0}

    def get_context(self, query=""):
        self.context_queries.append(query)
        return "memory context" if query == "with memory" else ""

    def set_event_callback(self, callback):
        self.callbacks.append(callback)

    def add_user_message(self, content):
        self.user_messages.append(content)
        self.buffer.messages.append(("user", content))

    def add_assistant_message(self, content):
        self.assistant_messages.append(content)
        self.buffer.messages.append(("assistant", content))

    def save(self):
        self.saved += 1

    def close_session(self):
        self.closed += 1
        return {"closed": True}

    def consume_events(self):
        events = self.events[:]
        self.events.clear()
        return events

    def compress_with_summary(self):
        if len(self.buffer.messages) < 4:
            return False
        self.compressor.summaries.append({"content": "summary"})
        self.buffer.messages = self.buffer.messages[-2:]
        return True

    def stats(self):
        return {"session_messages": len(self.buffer.messages)}

    def preview_context(self, query=""):
        return {"query": query, "recent_messages": len(self.buffer.messages)}

    def clear_session(self):
        return self.clear_result


class RaisingCloseMemory(FakeMemory):
    def close_session(self):
        self.closed += 1
        raise RuntimeError("close memory failed")


class FakeOrchestrator:
    def __init__(self, replies=None, pending=False):
        self.replies = list(replies or [])
        self.pending = pending
        self.handled = []
        self.turns = []

    def has_pending_confirmation(self):
        return self.pending

    def handle_pending_command_confirmation(self, user_input, event_callback=None):
        self.handled.append((user_input, event_callback))
        self.pending = False
        return self.replies.pop(0) if self.replies else "pending handled"

    def run_turn(self, user_input, event_callback=None):
        self.turns.append((user_input, event_callback))
        return self.replies.pop(0) if self.replies else "tool answer"


class FakeRunnerForAgent:
    def command_examples(self):
        return []

    def command_policy_summary(self):
        return ""


def make_agent(client=None, memory=None, **kwargs):
    kwargs.setdefault("tool_runner", FakeRunnerForAgent())
    return Agent(
        client=client or FakeClient(),
        memory=memory or FakeMemory(),
        config=AgentConfig(tool_orchestration_enabled=False),
        **kwargs,
    )


def test_agent_supports_minimal_text_client_without_optional_lifecycle_methods():
    client = MinimalTextClient("minimal answer")
    memory = FakeMemory()
    agent = make_agent(client, memory)

    agent.start()
    assert agent.run_turn("hello") == "minimal answer"
    assert agent.consume_notices() == []
    agent.close()

    assert client.sent_texts
    assert memory.assistant_messages == ["minimal answer"]
    assert memory.closed == 1


def test_agent_file_transport_contract_only_requires_send_file_for_long_prompt(
    tmp_path,
):
    client = MinimalFileClient("file answer")
    memory = FakeMemory()
    agent = make_agent(
        client,
        memory,
        max_text_chars=5,
        upload_dir=tmp_path / "uploads",
        file_transport_enabled=True,
    )

    assert agent.run_turn("long prompt") == "file answer"

    assert client.sent_texts == []
    assert len(client.sent_files) == 1
    assert Path(client.sent_files[0][0]).parent == tmp_path / "uploads"


def test_agent_lifecycle_start_close_idempotent_and_client_close_finally():
    client = FakeClient()
    memory = FakeMemory()
    agent = make_agent(client, memory)

    agent.start()
    agent.close()
    agent.close()

    assert client.started == 1
    assert memory.closed == 1
    assert client.closed == 1

    client = FakeClient()
    memory = RaisingCloseMemory()
    agent = make_agent(client, memory)
    with pytest.raises(RuntimeError):
        agent.close()
    assert client.closed == 1


def test_legacy_chat_path_updates_memory_with_markdown_cleaned_text():
    client = FakeClient(["# Title\n\n**bold** and `code`"])
    memory = FakeMemory()
    events = []
    agent = make_agent(client, memory)

    reply = agent.run_turn("hello", events.append)

    assert reply.startswith("# Title")
    assert memory.user_messages == ["hello"]
    assert memory.assistant_messages == ["Title\n\nbold and code"]
    assert memory.saved == 1
    assert memory.callbacks == [events.append, None]
    assert client.sent_texts


def test_legacy_chat_prompt_includes_memory_context():
    client = FakeClient(["reply"])
    memory = FakeMemory()
    agent = make_agent(client, memory)

    agent.run_turn("with memory")

    assert "memory context" in client.sent_texts[0]
    assert "with memory" in client.sent_texts[0]


def test_file_transport_integration_at_agent_boundary(tmp_path):
    client = FakeClient(["file response"])
    memory = FakeMemory()
    agent = make_agent(
        client,
        memory,
        max_text_chars=5,
        upload_dir=tmp_path / "uploads",
        file_transport_enabled=True,
    )
    events = []

    assert agent.run_turn("long prompt", events.append) == "file response"

    assert client.sent_texts == []
    assert len(client.sent_files) == 1
    path, prompt = client.sent_files[0]
    assert Path(path).parent == tmp_path / "uploads"
    assert Path(path).read_text(encoding="utf-8")
    assert prompt and "当前用户输入" in prompt
    assert {event["type"] for event in events} == {"file_transporting"}


def test_pending_confirmation_delegated_before_new_turn_and_stored():
    client = FakeClient()
    memory = FakeMemory()
    agent = make_agent(client, memory, workspace=object())
    orchestrator = FakeOrchestrator(["confirmed answer"], pending=True)
    agent.tool_orchestration_enabled = True
    agent.tool_orchestrator = orchestrator

    assert agent.run_turn("y") == "confirmed answer"

    assert orchestrator.handled and not orchestrator.turns
    assert memory.user_messages == ["y"]
    assert memory.assistant_messages == ["confirmed answer"]


def test_orchestrator_none_or_router_sentinel_falls_back_to_legacy_chat():
    client = FakeClient(["legacy reply"])
    memory = FakeMemory()
    agent = make_agent(client, memory, workspace=object())
    agent.tool_orchestration_enabled = True
    agent.tool_orchestrator = FakeOrchestrator([None])

    assert agent.run_turn("hello") == "legacy reply"
    assert client.sent_texts

    client = FakeClient(["legacy after sentinel"])
    memory = FakeMemory()
    agent = make_agent(client, memory, workspace=object())
    agent.tool_orchestration_enabled = True
    agent.tool_orchestrator = FakeOrchestrator(["terminal"])

    assert agent.run_turn("hello") == "legacy after sentinel"


def test_non_sentinel_tool_answer_stored_without_qwen():
    memory = FakeMemory()
    agent = make_agent(FakeClient(), memory, workspace=object())
    agent.tool_orchestration_enabled = True
    agent.tool_orchestrator = FakeOrchestrator(["tool final"])

    assert agent.run_turn("show files") == "tool final"
    assert memory.user_messages == ["show files"]
    assert memory.assistant_messages == ["tool final"]


def test_agent_delegation_methods():
    client = FakeClient()
    memory = FakeMemory()
    memory.buffer.messages = [1, 2, 3, 4]
    agent = make_agent(client, memory)

    assert agent.consume_events() == [{"type": "memory"}]
    assert agent.consume_notices() == [{"type": "notice", "message": "hi"}]
    assert agent.compress_memory()["status"] == "completed"
    assert agent.memory_status() == {"session_messages": 2}
    assert agent.memory_preview("q") == {"query": "q", "recent_messages": 2}
    assert agent.clear_session_memory() == {"cleared": 1, "remaining": 0}


def test_compress_memory_skip_and_failed_paths():
    memory = FakeMemory()
    agent = make_agent(FakeClient(), memory)
    assert agent.compress_memory()["status"] == "skipped"

    memory.buffer.messages = [1, 2, 3, 4]
    memory.compress_with_summary = lambda: False
    result = agent.compress_memory()
    assert result["status"] == "failed"
    assert result["message_count"] == 4
