import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from utils.text.generated_file import clean_generated_file_content

from .config import ToolConfig
from .prompt_loader import render_prompt
from .tool_commands import ReadonlyToolCommandRunner


@dataclass
class PendingCommandConfirmation:
    command: str
    argv: list[str]
    observations: list[dict]
    user_input: str
    used_tool: bool
    explicit_workspace_request: bool
    file_edit_id: str | None = None
    is_pipeline: bool = False
    pipeline_segments: list[list[str]] | None = None


class ToolOrchestrator:
    def __init__(
        self,
        client: Any,
        tool_runner: ReadonlyToolCommandRunner,
        max_tool_steps: int,
        emit_event: Callable[[Optional[Callable[[dict], None]], dict], None],
        config: ToolConfig | None = None,
    ):
        self.client = client
        self.tool_runner = tool_runner
        self.max_tool_steps = max(1, int(max_tool_steps))
        self.emit_event = emit_event
        self.config = config or ToolConfig()
        self.pending_command_confirmation: PendingCommandConfirmation | None = None

    def has_pending_confirmation(self) -> bool:
        return self.pending_command_confirmation is not None

    def run_turn(
        self, user_input: str, event_callback: Optional[Callable[[dict], None]] = None
    ) -> Optional[str]:
        use_terminal_tools = self.should_use_terminal_tools(user_input, event_callback)
        if not use_terminal_tools:
            return None
        return self._continue_tool_orchestration(
            user_input, [], False, use_terminal_tools, event_callback
        )

    def handle_pending_command_confirmation(
        self,
        user_input: str,
        event_callback: Optional[Callable[[dict], None]] = None,
    ) -> str:
        pending = self.pending_command_confirmation
        answer = user_input.strip().lower()
        if answer not in self.config.confirm_command_replies:
            if pending.file_edit_id:
                self.tool_runner.discard_file_edit(pending.file_edit_id)
            self.pending_command_confirmation = None
            return f"已取消命令：{pending.command}"

        self.pending_command_confirmation = None
        if pending.file_edit_id:
            observation = self.tool_runner.commit_file_edit(pending.file_edit_id)
            fallback = observation.get("stdout") or "文件修改已执行。"
        else:
            action = {
                "action": "command",
                "command": pending.command,
                "argv": pending.argv,
                "is_pipeline": pending.is_pipeline,
                "pipeline_segments": pending.pipeline_segments,
            }
            observation = self._execute_action(action, event_callback)
            fallback = "命令已执行。"

        observations = [*pending.observations, observation]
        return (
            self._continue_tool_orchestration(
                pending.user_input or f"用户已确认执行命令: {pending.command}",
                observations,
                True,
                pending.explicit_workspace_request,
                event_callback,
            )
            or fallback
        )

    def should_use_terminal_tools(
        self,
        user_input: str,
        event_callback: Optional[Callable[[dict], None]] = None,
    ) -> bool:
        self.emit_event(event_callback, {"type": "tool_routing"})
        try:
            reply = (
                self.client.send_text(self._build_tool_router_prompt(user_input))
                .strip()
                .lower()
            )
        except Exception:
            return self._looks_like_workspace_read_request(user_input)
        if reply in self.config.tool_router_positive_replies:
            return True
        if reply in self.config.tool_router_negative_replies:
            return False
        return self._looks_like_workspace_read_request(user_input)

    def _build_tool_router_prompt(self, user_input: str) -> str:
        return render_prompt("tool_router.md", user_input=user_input)

    def _looks_like_workspace_read_request(self, user_input: str) -> bool:
        text = user_input.lower()
        return any(verb in text for verb in self.config.workspace_read_verbs) and any(
            target in text for target in self.config.workspace_targets
        )

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
                action = self.tool_runner.validate_action(
                    self.tool_runner.parse_planner_reply(reply)
                )
            except ValueError as exc:
                if not used_tool and not explicit_workspace_request:
                    return None
                return f"终端命令 {reply} 被拒绝，未执行任何本地命令：{exc}"

            if action["action"] == "final":
                if not used_tool and not explicit_workspace_request:
                    return None
                if (used_tool or explicit_workspace_request) and not action.get(
                    "explicit_final", True
                ):
                    return "终端命令规划器未返回有效命令，未执行任何本地命令。"
                return (
                    action["answer"].strip()
                    or "已完成终端读取，但工具规划器没有生成回答。"
                )

            if self._is_file_replace_action(action):
                return self._prepare_file_replace_confirmation(
                    action,
                    user_input,
                    observations,
                    used_tool,
                    explicit_workspace_request,
                    event_callback,
                )

            if action.get("requires_confirmation"):
                self.pending_command_confirmation = PendingCommandConfirmation(
                    command=action["command"],
                    argv=action["argv"],
                    observations=observations,
                    user_input=user_input,
                    used_tool=used_tool,
                    explicit_workspace_request=explicit_workspace_request,
                    is_pipeline=action.get("is_pipeline", False),
                    pipeline_segments=action.get("pipeline_segments"),
                )
                if action["argv"][:2] == ["checkpoint", "restore"]:
                    return f"准备恢复 checkpoint `{action['argv'][2]}`，这会覆盖当前文件内容。{self._confirmation_prompt()}"
                return f"命令 `{action['command']}` 不在自动执行白名单内。{self._confirmation_prompt()}"

            observations.append(self._execute_action(action, event_callback))
            used_tool = True

        return self._force_final_answer(user_input, observations, event_callback)

    def _build_tool_planner_prompt(
        self, user_input: str, observations: list[dict], force_final: bool = False
    ) -> str:
        observations_json = json.dumps(observations, ensure_ascii=False, indent=2)
        force_final_rule = (
            "\n- 本轮已经达到终端命令调用上限，必须返回 final: 最终回答，不得继续请求命令。"
            if force_final
            else ""
        )
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

    def _is_file_replace_action(self, action: dict) -> bool:
        argv = action.get("argv", [])
        return len(argv) == 3 and argv[0] == "file" and argv[1] == "replace"

    def _prepare_file_replace_confirmation(
        self,
        action: dict,
        user_input: str,
        observations: list[dict],
        used_tool: bool,
        explicit_workspace_request: bool,
        event_callback: Optional[Callable[[dict], None]] = None,
    ) -> str:
        self.emit_event(event_callback, {"type": "file_edit_drafting"})
        content_prompt = self._build_file_content_prompt(
            user_input, action["argv"][2], observations
        )
        content = self._strip_file_content(self.client.send_text(content_prompt))
        prepared = self.tool_runner.prepare_file_replace(action["command"], content)
        self.pending_command_confirmation = PendingCommandConfirmation(
            command=action["command"],
            argv=action["argv"],
            observations=observations,
            user_input=user_input,
            used_tool=used_tool,
            explicit_workspace_request=explicit_workspace_request,
            file_edit_id=prepared["edit_id"],
        )
        diff_preview = (
            prepared["diff"][: self.config.file_edit_diff_preview_chars]
            or "（新旧内容无差异）"
        )
        return (
            f"准备修改文件 `{prepared['path']}`（{prepared['old_lines']} 行 -> {prepared['new_lines']} 行）。\n"
            "执行前会自动创建 checkpoint。Diff 预览:\n"
            f"```diff\n{diff_preview}\n```\n"
            f"{self._confirmation_prompt()}"
        )

    def _confirmation_prompt(self) -> str:
        confirm_reply = self.config.confirm_command_replies[0]
        return f"请回复 {confirm_reply} 执行，或回复 {self.config.cancel_command_reply} 取消。"

    def _build_file_content_prompt(
        self, user_input: str, path: str, observations: list[dict]
    ) -> str:
        observations_json = json.dumps(observations, ensure_ascii=False, indent=2)
        return render_prompt(
            "file_replace.md",
            user_input=user_input,
            path=path,
            observations_json=observations_json,
        )

    def _strip_file_content(self, text: str) -> str:
        return clean_generated_file_content(text)

    def _force_final_answer(
        self,
        user_input: str,
        observations: list[dict],
        event_callback: Optional[Callable[[dict], None]] = None,
    ) -> str:
        self.emit_event(event_callback, {"type": "tool_planning"})
        prompt = self._build_tool_planner_prompt(
            user_input, observations, force_final=True
        )
        reply = self.client.send_text(prompt)
        try:
            action = self.tool_runner.validate_action(
                self.tool_runner.parse_planner_reply(reply)
            )
        except ValueError:
            return self.config.tool_step_limit_message
        if action["action"] == "final":
            return action["answer"].strip() or self.config.tool_step_limit_message
        return self.config.tool_step_limit_message

    def _execute_action(
        self, action: dict, event_callback: Optional[Callable[[dict], None]] = None
    ) -> dict:
        self._emit_tool_event(action, event_callback)
        observation = self.tool_runner.execute(action)
        return self.tool_runner.truncate_observation(observation)

    def _emit_tool_event(
        self, action: dict, event_callback: Optional[Callable[[dict], None]] = None
    ) -> None:
        command = action.get("command", "")
        argv = action.get("argv", [])
        if argv and argv[0] == "cd":
            self.emit_event(
                event_callback, {"type": "tool_changing_dir", "command": command}
            )
        else:
            self.emit_event(
                event_callback, {"type": "tool_running_command", "command": command}
            )
