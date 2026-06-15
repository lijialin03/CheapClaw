# agent_core/tool_commands.py
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from utils.text_helpers import first_nonempty_line, strip_code_fence, truncate_text_fields

from .workspace import Workspace, WorkspaceError


COMMAND_NAME_PATTERN = re.compile(r"[A-Za-z0-9._+-]+")
DEFAULT_BLACKLIST = frozenset({"rm"})
DEFAULT_WHITELIST = frozenset({"ls", "cd", "cat"})
DEFAULT_SHELL_OPERATORS = frozenset({"|", "||", "&", "&&", ";", ">", ">>", "<", "<<", "<<<"})
OBSERVATION_TEXT_LIMIT = 8000


class ToolCommandError(ValueError):
    pass


@dataclass(frozen=True)
class TerminalCommand:
    command: str
    argv: list[str]


class TerminalCommandPolicy:
    """受控终端命令策略：解析、黑白名单校验和确认判断。"""

    def __init__(
        self,
        blacklist: Iterable[str] = DEFAULT_BLACKLIST,
        whitelist: Iterable[str] = DEFAULT_WHITELIST,
        shell_operators: Iterable[str] = DEFAULT_SHELL_OPERATORS,
    ):
        self.blacklist = frozenset(blacklist)
        self.whitelist = frozenset(whitelist)
        self.shell_operators = frozenset(shell_operators)

    def summary(self) -> str:
        blacklist = ", ".join(sorted(self.blacklist)) or "无"
        whitelist = ", ".join(sorted(self.whitelist)) or "无"
        if self.whitelist:
            return f"当前黑名单: {blacklist}；当前自动执行白名单: {whitelist}；其他非黑名单命令需要用户确认。"
        return f"当前黑名单: {blacklist}；未配置自动执行白名单，所有非黑名单命令都需要用户确认。"

    def examples(self) -> list[str]:
        examples = ["ls"]
        if "ls" in self.whitelist:
            examples.append("ls agent_core")
        if "cd" in self.whitelist:
            examples.extend(["cd agent_core", "cd .."])
        if "cat" in self.whitelist:
            examples.append("cat agent_core/agent.py")
        examples.append("final: 当前目录包含 main.py、agent_core、llm、ui 等。")
        return examples

    def validate_action(self, data: dict) -> dict:
        if not isinstance(data, dict):
            raise ToolCommandError("工具规划器结果必须是对象")
        action = data.get("action")
        if action == "final":
            answer = data.get("answer")
            if not isinstance(answer, str):
                raise ToolCommandError("final.answer 必须是字符串")
            return {"action": "final", "answer": answer, "explicit_final": data.get("explicit_final", True)}
        if action != "command":
            raise ToolCommandError("action 必须是 command 或 final")
        command = self.validate_command(data.get("command", ""))
        action = {"action": "command", "command": command.command, "argv": command.argv}
        if self.requires_confirmation(command):
            action["requires_confirmation"] = True
        return action

    def validate_command(self, command_text: str) -> TerminalCommand:
        if not isinstance(command_text, str) or not command_text.strip():
            raise ToolCommandError("命令必须是非空字符串")
        argv = shlex.split(command_text.strip())
        if not argv:
            raise ToolCommandError("命令必须是非空字符串")
        program = Path(argv[0]).name
        if not COMMAND_NAME_PATTERN.fullmatch(program):
            raise ToolCommandError(f"命令名非法: {program}")
        if program in self.blacklist:
            raise ToolCommandError(f"命令在黑名单内: {program}")
        self.reject_shell_structures(argv)
        normalized_argv = [program, *argv[1:]]
        return TerminalCommand(command=shlex.join(normalized_argv), argv=normalized_argv)

    def requires_confirmation(self, command: TerminalCommand | dict) -> bool:
        argv = command.argv if isinstance(command, TerminalCommand) else command.get("argv", [])
        return bool(argv) and argv[0] not in self.whitelist

    def looks_like_shell_command(self, text: str) -> bool:
        try:
            argv = shlex.split(text)
        except ValueError:
            return True
        if not argv:
            return False
        program = Path(argv[0]).name
        if "=" in argv[0] and argv[0].split("=", 1)[0].isidentifier():
            return True
        if program in self.blacklist or program in self.whitelist:
            return True
        return bool(COMMAND_NAME_PATTERN.fullmatch(program)) and (
            len(argv) > 1 or "/" in argv[0] or text.startswith("./")
        )

    def reject_shell_structures(self, argv: Iterable[str]) -> None:
        for token in argv:
            if token in self.shell_operators or "$" in token or "`" in token:
                raise ToolCommandError("不允许 shell 管道、重定向、后台执行、命令替换或变量展开")
            if "=" in token and not token.startswith("-") and token.split("=", 1)[0].isidentifier():
                raise ToolCommandError("不允许环境变量赋值")


class ControlledTerminalRunner:
    """受控终端执行层：维护 cwd，并执行经策略校验后的终端命令。"""

    BLACKLIST = set(DEFAULT_BLACKLIST)
    WHITELIST = set(DEFAULT_WHITELIST)
    SHELL_OPERATORS = set(DEFAULT_SHELL_OPERATORS)

    def __init__(
        self,
        workspace: Workspace,
        cwd: str | Path | None = None,
        policy: TerminalCommandPolicy | None = None,
    ):
        self.workspace = workspace
        self.cwd = workspace.resolve(str(cwd or "."))
        self.policy = policy or TerminalCommandPolicy(
            blacklist=self.BLACKLIST,
            whitelist=self.WHITELIST,
            shell_operators=self.SHELL_OPERATORS,
        )

    def command_policy_summary(self) -> str:
        return self.policy.summary()

    def command_examples(self) -> list[str]:
        return self.policy.examples()

    def parse_planner_reply(self, reply: str) -> dict:
        text = strip_code_fence(reply.strip())
        if not text:
            raise ToolCommandError("工具规划器未返回内容")
        if text.lower().startswith("final:"):
            return {"action": "final", "answer": text.split(":", 1)[1].strip(), "explicit_final": True}

        command_text = first_nonempty_line(text)
        try:
            command = self.validate_command(command_text)
        except ToolCommandError:
            if self.policy.looks_like_shell_command(command_text):
                raise
            return {"action": "final", "answer": text, "explicit_final": False}
        return {"action": "command", "command": command.command, "argv": command.argv}

    def validate_action(self, data: dict) -> dict:
        return self.policy.validate_action(data)

    def validate_command(self, command_text: str) -> TerminalCommand:
        return self.policy.validate_command(command_text)

    def requires_confirmation(self, command: TerminalCommand | dict) -> bool:
        return self.policy.requires_confirmation(command)

    def execute(self, action: dict | TerminalCommand) -> dict:
        command = action if isinstance(action, TerminalCommand) else TerminalCommand(action["command"], action["argv"])
        try:
            if command.argv[0] == "cd":
                return self._execute_cd(command)
            return self._execute_subprocess(command)
        except (ToolCommandError, WorkspaceError, FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
            return {
                "command": command.command,
                "ok": False,
                "cwd": self._relative_cwd(),
                "error": str(exc),
            }

    def truncate_observation(self, observation: dict) -> dict:
        return truncate_text_fields(observation, ("stdout", "stderr"), OBSERVATION_TEXT_LIMIT)

    def _execute_cd(self, command: TerminalCommand) -> dict:
        if len(command.argv) > 2:
            raise ToolCommandError("cd 只支持一个目标路径")
        target = command.argv[1] if len(command.argv) == 2 else "."
        next_cwd = self._resolve_from_cwd(target)
        if not next_cwd.exists():
            raise WorkspaceError(f"路径不存在: {target}")
        if not next_cwd.is_dir():
            raise WorkspaceError(f"不是目录: {target}")
        self.cwd = next_cwd
        return {
            "command": command.command,
            "ok": True,
            "cwd": self._relative_cwd(),
            "stdout": self._relative_cwd(),
            "stderr": "",
            "returncode": 0,
        }

    def _execute_subprocess(self, command: TerminalCommand) -> dict:
        result = subprocess.run(
            command.argv,
            cwd=str(self.cwd),
            text=True,
            capture_output=True,
            timeout=10,
            shell=False,
        )
        return {
            "command": command.command,
            "ok": result.returncode == 0,
            "cwd": self._relative_cwd(),
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    def _resolve_from_cwd(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.cwd / candidate
        return self.workspace.resolve(str(candidate))

    def _relative_cwd(self) -> str:
        try:
            return str(self.cwd.relative_to(self.workspace.root)) or "."
        except ValueError:
            return str(self.cwd)


ReadonlyToolCommandRunner = ControlledTerminalRunner
