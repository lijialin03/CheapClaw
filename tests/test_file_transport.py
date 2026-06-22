from pathlib import Path

from agent_core.file_transport import PromptTransport


class FakeClient:
    def __init__(self):
        self.texts = []
        self.files = []

    def send_text(self, text, **kwargs):
        self.texts.append((text, kwargs))
        return "text response"

    def send_file(self, file_path, prompt=None):
        self.files.append((file_path, prompt))
        return "file response"


def make_transport(client, upload_dir, enabled=True, max_text_chars=10, events=None):
    def emit(callback, event):
        if events is not None:
            events.append(event)
        if callback:
            callback(event)

    return PromptTransport(
        client=client,
        upload_dir=upload_dir,
        max_text_chars=max_text_chars,
        file_transport_enabled=enabled,
        emit_event=emit,
    )


def test_short_prompts_use_send_text(tmp_path):
    client = FakeClient()
    transport = make_transport(client, tmp_path / "uploads")

    assert transport.send("short") == "text response"

    assert client.texts == [("short", {})]
    assert client.files == []
    assert not (tmp_path / "uploads").exists()


def test_long_prompts_use_send_text_when_file_transport_disabled(tmp_path):
    client = FakeClient()
    transport = make_transport(
        client, tmp_path / "uploads", enabled=False, max_text_chars=3
    )

    assert transport.send("very long prompt") == "text response"

    assert client.texts[0][0] == "very long prompt"
    assert client.files == []
    assert not (tmp_path / "uploads").exists()


def test_long_prompts_write_local_temp_file_and_use_send_file(tmp_path):
    client = FakeClient()
    events = []
    callback_events = []
    upload_dir = tmp_path / "uploads"
    transport = make_transport(
        client, upload_dir, enabled=True, max_text_chars=3, events=events
    )

    assert transport.send("very long prompt", callback_events.append) == "file response"

    assert client.texts == []
    assert len(client.files) == 1
    file_path, prompt = client.files[0]
    prompt_file = Path(file_path)
    assert prompt_file.parent == upload_dir
    assert prompt_file.name.startswith("prompt-")
    assert prompt_file.suffix == ".txt"
    assert prompt_file.read_text(encoding="utf-8") == "very long prompt"
    assert sorted(path.name for path in upload_dir.iterdir()) == [prompt_file.name]
    assert prompt and "当前用户输入" in prompt
    assert events == [{"type": "file_transporting"}]
    assert callback_events == [{"type": "file_transporting"}]
