import pytest

from agent_core.config import ToolConfig
from agent_core.tool_orchestrator import ToolOrchestrator


class QueueClient:
    def __init__(self, replies=None, error=False):
        self.replies = list(replies or [])
        self.prompts = []
        self.error = error

    def send_text(self, text, **kwargs):
        self.prompts.append(text)
        if self.error:
            raise RuntimeError("router failed")
        if not self.replies:
            raise AssertionError("No queued reply")
        return self.replies.pop(0)


class FakeRunner:
    def __init__(self):
        self.executed = []
        self.discarded = []
        self.committed = []
        self.prepared = []
        self.observations = []

    def command_examples(self):
        return ["ls", "final: done"]

    def command_policy_summary(self):
        return "policy summary"

    def parse_planner_reply(self, reply):
        text = reply.strip()
        if text.startswith("INVALID"):
            raise ValueError("invalid planner")
        if text.startswith("final:"):
            return {"action": "final", "answer": text.split(":", 1)[1].strip(), "explicit_final": True}
        if text.startswith("prose:"):
            return {"action": "final", "answer": text.split(":", 1)[1].strip(), "explicit_final": False}
        argv = text.split()
        return {"action": "command", "command": text, "argv": argv}

    def validate_action(self, data):
        if data["action"] == "command" and data["argv"][0] in {"python", "checkpoint"}:
            data = dict(data)
            data["requires_confirmation"] = True
        return data

    def execute(self, action):
        self.executed.append(action)
        observation = {
            "command": action["command"],
            "ok": True,
            "stdout": f"out:{action['command']}",
            "stderr": "",
            "returncode": 0,
        }
        self.observations.append(observation)
        return observation

    def truncate_observation(self, observation):
        compact = dict(observation)
        if len(compact.get("stdout", "")) > 12:
            compact["stdout"] = compact["stdout"][:12]
        return compact

    def prepare_file_replace(self, command, content):
        prepared = {
            "edit_id": "edit-1",
            "command": command,
            "path": command.split()[-1],
            "diff": "D" * 50,
            "old_lines": 1,
            "new_lines": len(content.splitlines()),
            "content": content,
        }
        self.prepared.append(prepared)
        return prepared

    def commit_file_edit(self, edit_id):
        self.committed.append(edit_id)
        return {"command": "file replace a.py", "ok": True, "stdout": "committed", "stderr": "", "returncode": 0}

    def discard_file_edit(self, edit_id):
        self.discarded.append(edit_id)


def make_orchestrator(client, runner=None, max_tool_steps=3, config=None, events=None):
    events = events if events is not None else []

    def emit(callback, event):
        events.append(event)
        if callback:
            callback(event)

    return ToolOrchestrator(
        client=client,
        tool_runner=runner or FakeRunner(),
        max_tool_steps=max_tool_steps,
        emit_event=emit,
        config=config or ToolConfig(file_edit_diff_preview_chars=10),
    )


def test_router_positive_and_negative_decisions():
    events = []
    yes = make_orchestrator(QueueClient(["terminal"]), events=events)
    no = make_orchestrator(QueueClient(["chat"]))

    assert yes.should_use_terminal_tools("read files") is True
    assert no.should_use_terminal_tools("hello") is False
    assert events == [{"type": "tool_routing"}]


def test_router_fallback_on_errors_and_unknown_text():
    assert make_orchestrator(QueueClient(error=True)).should_use_terminal_tools("please read agent_core") is True
    assert make_orchestrator(QueueClient(["maybe"])).should_use_terminal_tools("hello") is False
    assert make_orchestrator(QueueClient(["maybe"])).should_use_terminal_tools("show run.py") is True


def test_command_execution_loop_followed_by_final_answer():
    events = []
    runner = FakeRunner()
    client = QueueClient(["terminal", "ls", "final: done"])
    orchestrator = make_orchestrator(client, runner=runner, events=events)

    answer = orchestrator.run_turn("list files")

    assert answer == "done"
    assert [action["command"] for action in runner.executed] == ["ls"]
    assert [event["type"] for event in events] == ["tool_routing", "tool_planning", "tool_running_command", "tool_planning"]


def test_invalid_planner_output_returns_none_for_non_explicit_tool_request():
    orchestrator = make_orchestrator(QueueClient(["INVALID"]))

    assert orchestrator._continue_tool_orchestration("hello", [], False, False) is None


def test_invalid_planner_output_reports_rejection_for_explicit_request():
    orchestrator = make_orchestrator(QueueClient(["INVALID"]))

    answer = orchestrator._continue_tool_orchestration("show run.py", [], False, True)

    assert "终端命令被拒绝" in answer
    assert "invalid planner" in answer


def test_pending_confirmation_confirm_and_cancel_paths():
    runner = FakeRunner()
    client = QueueClient(["terminal", "python script.py", "final: after confirm"])
    orchestrator = make_orchestrator(client, runner=runner)

    prompt = orchestrator.run_turn("run script")
    assert "不在自动执行白名单" in prompt
    assert orchestrator.has_pending_confirmation()

    answer = orchestrator.handle_pending_command_confirmation("y")
    assert answer == "after confirm"
    assert runner.executed[0]["command"] == "python script.py"
    assert not orchestrator.has_pending_confirmation()

    client = QueueClient(["terminal", "python script.py"])
    runner = FakeRunner()
    orchestrator = make_orchestrator(client, runner=runner)
    orchestrator.run_turn("run script")
    assert orchestrator.handle_pending_command_confirmation("no") == "已取消命令：python script.py"
    assert runner.executed == []


def test_checkpoint_restore_confirmation_wording():
    orchestrator = make_orchestrator(QueueClient(["terminal", "checkpoint restore ckpt-1"]))

    prompt = orchestrator.run_turn("restore checkpoint")

    assert "准备恢复 checkpoint `ckpt-1`" in prompt
    assert orchestrator.has_pending_confirmation()


def test_file_replace_generates_clean_content_truncated_preview_confirm_and_cancel():
    runner = FakeRunner()
    config = ToolConfig(file_edit_diff_preview_chars=10)
    client = QueueClient(["terminal", "file replace a.py", "```python\n1 print('x')\n```"])
    orchestrator = make_orchestrator(client, runner=runner, config=config)

    prompt = orchestrator.run_turn("replace a.py")

    assert "准备修改文件 `a.py`" in prompt
    assert "D" * 10 in prompt
    assert "D" * 11 not in prompt
    assert runner.prepared[0]["content"] == "print('x')"
    assert orchestrator.has_pending_confirmation()

    answer = orchestrator.handle_pending_command_confirmation("yes")
    assert answer == "committed"
    assert runner.committed == ["edit-1"]
    assert client.replies == []

    runner = FakeRunner()
    client = QueueClient(["terminal", "file replace a.py", "new content"])
    orchestrator = make_orchestrator(client, runner=runner, config=config)
    orchestrator.run_turn("replace a.py")
    cancel = orchestrator.handle_pending_command_confirmation("no")
    assert cancel == "已取消命令：file replace a.py"
    assert runner.discarded == ["edit-1"]


def test_file_replace_preserves_yaml_generated_content():
    runner = FakeRunner()
    rendered_yaml = "yaml\n1\n2\n3\n4\nrepos:\n  - repo: x\n    hooks:\n      - id: check-yaml"
    client = QueueClient(["terminal", "file replace .pre-commit-config.yaml", rendered_yaml])
    orchestrator = make_orchestrator(client, runner=runner)

    prompt = orchestrator.run_turn("replace pre-commit config")

    assert "准备修改文件 `.pre-commit-config.yaml`" in prompt
    assert runner.prepared[0]["content"] == "repos:\n  - repo: x\n    hooks:\n      - id: check-yaml"


def test_max_tool_step_forced_final_behavior():
    runner = FakeRunner()
    client = QueueClient(["terminal", "ls", "final: forced answer"])
    orchestrator = make_orchestrator(client, runner=runner, max_tool_steps=1)

    assert orchestrator.run_turn("list files") == "forced answer"

    client = QueueClient(["terminal", "ls", "ls"])
    orchestrator = make_orchestrator(client, runner=FakeRunner(), max_tool_steps=1)
    assert orchestrator.run_turn("list files") == ToolConfig().tool_step_limit_message
