# bot/tool_commands.py
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .workspace import Workspace, WorkspaceError


class ToolCommandError(ValueError):
    pass


@dataclass(frozen=True)
class TerminalCommand:
    command: str
    argv: list[str]


class ControlledTerminalRunner:
    """受控终端命令层：LLM bash 命令文本 -> 黑白名单校验 -> 终端/内置执行。"""

    BLACKLIST = {"rm"}
    WHITELIST = {"ls", "cd", "cat"}
    SHELL_OPERATORS = {"|", "||", "&", "&&", ";", ">", ">>", "<", "<<", "<<<"}

    def __init__(self, workspace: Workspace, cwd: str | Path | None = None):
        self.workspace = workspace
        self.cwd = workspace.resolve(str(cwd or "."))

    def command_policy_summary(self) -> str:
        blacklist = ", ".join(sorted(self.BLACKLIST)) or "无"
        whitelist = ", ".join(sorted(self.WHITELIST)) or "无"
        if self.WHITELIST:
            return f"当前黑名单: {blacklist}；当前自动执行白名单: {whitelist}；其他非黑名单命令需要用户确认。"
        return f"当前黑名单: {blacklist}；未配置自动执行白名单，所有非黑名单命令都需要用户确认。"

    def command_examples(self) -> list[str]:
        examples = ["ls", "ls bot", "cd bot", "cd .."]
        if "cat" in self.WHITELIST:
            examples.append("cat agent.log")
        examples.append("final: 当前目录包含 main.py、bot、llm、ui 等。")
        return examples

    def parse_planner_reply(self, reply: str) -> dict:
        text = self._strip_code_fence(reply.strip())
        if not text:
            raise ToolCommandError("工具规划器未返回内容")
        if text.lower().startswith("final:"):
            return {"action": "final", "answer": text.split(":", 1)[1].strip(), "explicit_final": True}

        command_text = self._first_nonempty_line(text)
        try:
            command = self.validate_command(command_text)
        except ToolCommandError:
            if self._looks_like_shell_command(command_text):
                raise
            return {"action": "final", "answer": text, "explicit_final": False}
        return {"action": "command", "command": command.command, "argv": command.argv}

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
        if not re.fullmatch(r"[A-Za-z0-9._+-]+", program):
            raise ToolCommandError(f"命令名非法: {program}")
        if program in self.BLACKLIST:
            raise ToolCommandError(f"命令在黑名单内: {program}")
        self._reject_shell_structures(argv)
        normalized_argv = [program, *argv[1:]]
        return TerminalCommand(command=shlex.join(normalized_argv), argv=normalized_argv)

    def requires_confirmation(self, command: TerminalCommand | dict) -> bool:
        argv = command.argv if isinstance(command, TerminalCommand) else command.get("argv", [])
        return bool(argv) and argv[0] not in self.WHITELIST

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
        compact = dict(observation)
        for key in ("stdout", "stderr"):
            value = compact.get(key)
            if isinstance(value, str) and len(value) > 8000:
                compact[key] = value[:8000] + "\n...[truncated]"
                compact[f"truncated_{key}_chars"] = len(value) - 8000
        return compact

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

    def _reject_shell_structures(self, argv: Iterable[str]) -> None:
        for token in argv:
            if token in self.SHELL_OPERATORS or "$" in token or "`" in token:
                raise ToolCommandError("不允许 shell 管道、重定向、后台执行、命令替换或变量展开")
            if "=" in token and not token.startswith("-") and token.split("=", 1)[0].isidentifier():
                raise ToolCommandError("不允许环境变量赋值")

    def _looks_like_shell_command(self, text: str) -> bool:
        try:
            argv = shlex.split(text)
        except ValueError:
            return True
        if not argv:
            return False
        program = Path(argv[0]).name
        if "=" in argv[0] and argv[0].split("=", 1)[0].isidentifier():
            return True
        if program in self.BLACKLIST or program in self.WHITELIST:
            return True
        return bool(re.fullmatch(r"[A-Za-z0-9._+-]+", program)) and (
            len(argv) > 1 or "/" in argv[0] or text.startswith("./")
        )

    def _strip_code_fence(self, text: str) -> str:
        if not text.startswith("```"):
            return text
        lines = text.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
        return text

    def _first_nonempty_line(self, text: str) -> str:
        for line in text.splitlines():
            if line.strip():
                return line.strip()
        return ""


ReadonlyToolCommandRunner = ControlledTerminalRunner
