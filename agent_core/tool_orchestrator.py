# agent_core/tool_orchestrator.py
import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .prompt_loader import render_prompt
from .tool_commands import ReadonlyToolCommandRunner


CONFIRM_COMMAND_REPLIES = {"y", "yes", "确认", "执行", "是"}
TOOL_STEP_LIMIT_MESSAGE = "已达到终端命令调用步数上限，无法继续读取更多信息。"
WORKSPACE_READ_VERBS = (
    "读取", "读", "查看", "检查", "列出", "看看", "打开", "分析", "总结",
    "review", "analyze", "read", "show", "list", "stat",
)
WORKSPACE_TARGETS = (
    "目录", "文件", "路径", "当前目录", "workspace", "main.py", ".py", ".json",
    ".md", ".txt", "/", "./", "agent_core", "ui", "model_clients", "config",
)


@dataclass
class PendingCommandConfirmation:
    command: str
    argv: list[str]
    observations: list[dict]
    user_input: str
    used_tool: bool
    explicit_workspace_request: bool


class ToolOrchestrator:
    def __init__(
        self,
        client: Any,
        tool_runner: ReadonlyToolCommandRunner,
        max_tool_steps: int,
        emit_event: Callable[[Optional[Callable[[dict], None]], dict], None],
    ):
        self.client = client
        self.tool_runner = tool_runner
        self.max_tool_steps = max(1, int(max_tool_steps))
        self.emit_event = emit_event
        self.pending_command_confirmation: PendingCommandConfirmation | None = None

    def has_pending_confirmation(self) -> bool:
        return self.pending_command_confirmation is not None

    def run_turn(self, user_input: str, event_callback: Optional[Callable[[dict], None]] = None) -> Optional[str]:
        use_terminal_tools = self.should_use_terminal_tools(user_input, event_callback)
        if not use_terminal_tools:
            return None
        return self._continue_tool_orchestration(user_input, [], False, use_terminal_tools, event_callback)

    def handle_pending_command_confirmation(
        self,
        user_input: str,
        event_callback: Optional[Callable[[dict], None]] = None,
    ) -> str:
        pending = self.pending_command_confirmation
        answer = user_input.strip().lower()
        if answer not in CONFIRM_COMMAND_REPLIES:
            self.pending_command_confirmation = None
            return f"已取消命令：{pending.command}"

        self.pending_command_confirmation = None
        action = {"action": "command", "command": pending.command, "argv": pending.argv}
        observations = [*pending.observations, self._execute_action(action, event_callback)]
        return self._continue_tool_orchestration(
            pending.user_input or f"用户已确认执行命令: {pending.command}",
            observations,
            True,
            pending.explicit_workspace_request,
            event_callback,
        ) or "命令已执行。"

    def should_use_terminal_tools(
        self,
        user_input: str,
        event_callback: Optional[Callable[[dict], None]] = None,
    ) -> bool:
        self.emit_event(event_callback, {"type": "tool_routing"})
        try:
            reply = self.client.send_text(self._build_tool_router_prompt(user_input)).strip().lower()
        except Exception:
            return self._looks_like_workspace_read_request(user_input)
        if reply in {"terminal", "tool", "tools", "yes"}:
            return True
        if reply in {"chat", "none", "no"}:
            return False
        return self._looks_like_workspace_read_request(user_input)

    def _build_tool_router_prompt(self, user_input: str) -> str:
        return render_prompt("tool_router.md", user_input=user_input)

    def _looks_like_workspace_read_request(self, user_input: str) -> bool:
        text = user_input.lower()
        return any(verb in text for verb in WORKSPACE_READ_VERBS) and any(target in text for target in WORKSPACE_TARGETS)

    def _continue_tool_orchestration(
        self,
        user_input: str,
        observations: list[dict],
        used_tool: bool,
        explicit_workspace_request: bool,
        event_callback: Optional[Callable[[dict], None]] = None,
    ) -> Optional[str]:
        for _ in range(self.max_tool_steps):
            self.emit_event(event_callback, {"type": "tool_planning"})
            prompt = self._build_tool_planner_prompt(user_input, observations)
            reply = self.client.send_text(prompt)
            try:
                action = self.tool_runner.validate_action(self.tool_runner.parse_planner_reply(reply))
            except ValueError as exc:
                if not used_tool and not explicit_workspace_request:
                    return None
                return f"终端命令被拒绝，未执行任何本地命令：{exc}"

            if action["action"] == "final":
                if not used_tool and not explicit_workspace_request:
                    return None
                if (used_tool or explicit_workspace_request) and not action.get("explicit_final", True):
                    return "终端命令规划器未返回有效命令，未执行任何本地命令。"
                return action["answer"].strip() or "已完成终端读取，但工具规划器没有生成回答。"

            if action.get("requires_confirmation"):
                self.pending_command_confirmation = PendingCommandConfirmation(
                    command=action["command"],
                    argv=action["argv"],
                    observations=observations,
                    user_input=user_input,
                    used_tool=used_tool,
                    explicit_workspace_request=explicit_workspace_request,
                )
                return f"命令 `{action['command']}` 不在自动执行白名单内。请回复 yes 执行，或回复 no 取消。"

            observations.append(self._execute_action(action, event_callback))
            used_tool = True

        return self._force_final_answer(user_input, observations, event_callback)

    def _build_tool_planner_prompt(self, user_input: str, observations: list[dict], force_final: bool = False) -> str:
        observations_json = json.dumps(observations, ensure_ascii=False, indent=2)
        force_final_rule = "\n- 本轮已经达到终端命令调用上限，必须返回 final: 最终回答，不得继续请求命令。" if force_final else ""
        examples = "\n".join(self.tool_runner.command_examples())
        policy = self.tool_runner.command_policy_summary()
        return render_prompt(
            "tool_planner.md",
            user_input=user_input,
            examples=examples,
            policy=policy,
            force_final_rule=force_final_rule,
            observations_json=observations_json,
        )

    def _force_final_answer(
        self,
        user_input: str,
        observations: list[dict],
        event_callback: Optional[Callable[[dict], None]] = None,
    ) -> str:
        self.emit_event(event_callback, {"type": "tool_planning"})
        prompt = self._build_tool_planner_prompt(user_input, observations, force_final=True)
        reply = self.client.send_text(prompt)
        try:
            action = self.tool_runner.validate_action(self.tool_runner.parse_planner_reply(reply))
        except ValueError:
            return TOOL_STEP_LIMIT_MESSAGE
        if action["action"] == "final":
            return action["answer"].strip() or TOOL_STEP_LIMIT_MESSAGE
        return TOOL_STEP_LIMIT_MESSAGE

    def _execute_action(self, action: dict, event_callback: Optional[Callable[[dict], None]] = None) -> dict:
        self._emit_tool_event(action, event_callback)
        observation = self.tool_runner.execute(action)
        return self.tool_runner.truncate_observation(observation)

    def _emit_tool_event(self, action: dict, event_callback: Optional[Callable[[dict], None]] = None) -> None:
        command = action.get("command", "")
        argv = action.get("argv", [])
        if argv and argv[0] == "cd":
            self.emit_event(event_callback, {"type": "tool_changing_dir", "command": command})
        else:
            self.emit_event(event_callback, {"type": "tool_running_command", "command": command})
