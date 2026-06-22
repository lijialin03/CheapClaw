from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

from .prompt_loader import render_prompt


class PromptTransport:
    def __init__(
        self,
        client: Any,
        upload_dir: str | Path,
        max_text_chars: int,
        file_transport_enabled: bool,
        emit_event: Callable[[Optional[Callable[[dict], None]], dict], None],
    ):
        self.client = client
        self.upload_dir = Path(upload_dir)
        self.max_text_chars = max_text_chars
        self.file_transport_enabled = file_transport_enabled
        self.emit_event = emit_event

    def send(
        self, prompt: str, event_callback: Optional[Callable[[dict], None]] = None
    ) -> str:
        if self.file_transport_enabled and len(prompt) > self.max_text_chars:
            self.emit_event(event_callback, {"type": "file_transporting"})
            prompt_file = self._write_upload_prompt(prompt)
            return self.client.send_file(
                str(prompt_file),
                prompt=render_prompt("file_transport.md"),
            )
        return self.client.send_text(prompt)

    def _write_upload_prompt(self, prompt: str) -> Path:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = self.upload_dir / f"prompt-{uuid4().hex}.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        return prompt_file
