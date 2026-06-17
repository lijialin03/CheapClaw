# agent_core/tool_commands.py
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from utils.text_helpers import first_nonempty_line, strip_code_fence, truncate_text_fields

from .config import ToolConfig
from .workspace import Workspace, WorkspaceError


COMMAND_NAME_PATTERN = re.compile(r"[A-Za-z0-9._+-]+")
DEFAULT_BUILTINS = frozenset({"file", "checkpoint"})
DEFAULT_SHELL_OPERATORS = frozenset({"|", "||", "&", "&&", ";", ">", ">>", "<", "<<", "<<<"})


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
        blacklist: Iterable[str] | None = None,
        whitelist: Iterable[str] | None = None,
        builtins: Iterable[str] = DEFAULT_BUILTINS,
        shell_operators: Iterable[str] = DEFAULT_SHELL_OPERATORS,
    ):
        defaults = ToolConfig()
        self.blacklist = frozenset(defaults.command_blacklist if blacklist is None else blacklist)
        self.whitelist = frozenset(defaults.command_whitelist if whitelist is None else whitelist)
        self.builtins = frozenset(builtins)
        self.shell_operators = frozenset(shell_operators)

    def summary(self) -> str:
        blacklist = ", ".join(sorted(self.blacklist)) or "无"
        whitelist = ", ".join(sorted(self.whitelist)) or "无"
        builtins = ", ".join(sorted(self.builtins)) or "无"
        base = f"当前黑名单: {blacklist}；当前自动执行白名单: {whitelist}；内置伪命令: {builtins}。"
        return f"{base} 其他非黑名单命令需要用户确认；file 写入和 checkpoint restore 一律需要用户确认。"

    def examples(self) -> list[str]:
        examples = ["ls"]
        if "ls" in self.whitelist:
            examples.append("ls agent_core")
        if "cd" in self.whitelist:
            examples.extend(["cd agent_core", "cd .."])
        if "cat" in self.whitelist:
            examples.append("cat agent_core/agent.py")
        examples.extend([
            "file replace debug/test.py",
            "checkpoint list",
            "checkpoint restore ckpt-example",
        ])
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
        command = TerminalCommand(command=shlex.join(normalized_argv), argv=normalized_argv)
        if program in self.builtins:
            self.validate_builtin(command)
        return command

    def requires_confirmation(self, command: TerminalCommand | dict) -> bool:
        argv = command.argv if isinstance(command, TerminalCommand) else command.get("argv", [])
        if not argv:
            return False
        if argv[0] == "file":
            return True
        if argv[0] == "checkpoint" and len(argv) > 1 and argv[1] == "restore":
            return True
        return argv[0] not in self.whitelist

    def validate_builtin(self, command: TerminalCommand) -> None:
        argv = command.argv
        if argv[0] == "file":
            if len(argv) != 3 or argv[1] != "replace":
                raise ToolCommandError("file 仅支持: file replace <path>")
            self._reject_unsafe_path_argument(argv[2])
            return
        if argv[0] == "checkpoint":
            if len(argv) == 2 and argv[1] == "list":
                return
            if len(argv) == 3 and argv[1] == "restore":
                return
            raise ToolCommandError("checkpoint 仅支持: checkpoint list 或 checkpoint restore <id>")
        raise ToolCommandError(f"未知内置伪命令: {argv[0]}")

    def _reject_unsafe_path_argument(self, path: str) -> None:
        if any(token in path for token in ("*", "?", "[", "]")):
            raise ToolCommandError("file 命令不支持通配符路径")

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

    def __init__(
        self,
        workspace: Workspace,
        cwd: str | Path | None = None,
        config: ToolConfig | None = None,
        policy: TerminalCommandPolicy | None = None,
    ):
        self.workspace = workspace
        self.cwd = workspace.resolve(str(cwd or "."))
        self.config = config or ToolConfig()
        self.policy = policy or TerminalCommandPolicy(
            blacklist=self.config.command_blacklist,
            whitelist=self.config.command_whitelist,
            shell_operators=DEFAULT_SHELL_OPERATORS,
        )
        self.session_id = uuid.uuid4().hex[:8]
        self.checkpoint_root = self.workspace.root / ".cheapclaw" / "checkpoints" / self.session_id
        self.pending_file_edits: dict[str, dict] = {}

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
            if command.argv[0] == "file":
                return self._execute_file_builtin(command)
            if command.argv[0] == "checkpoint":
                return self._execute_checkpoint_builtin(command)
            return self._execute_subprocess(command)
        except (ToolCommandError, WorkspaceError, FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
            return {
                "command": command.command,
                "ok": False,
                "cwd": self._relative_cwd(),
                "error": str(exc),
            }

    def truncate_observation(self, observation: dict) -> dict:
        return truncate_text_fields(observation, ("stdout", "stderr"), self.config.observation_text_limit)

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
            timeout=self.config.subprocess_timeout_seconds,
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

    def prepare_file_replace(self, command: str, content: str) -> dict:
        terminal_command = self.validate_command(command)
        if terminal_command.argv[0:2] != ["file", "replace"]:
            raise ToolCommandError("只支持准备 file replace <path>")
        target = self._resolve_from_cwd(terminal_command.argv[2])
        if target.exists() and not target.is_file():
            raise WorkspaceError(f"不是普通文件: {terminal_command.argv[2]}")
        encoded = content.encode("utf-8")
        if len(encoded) > self.config.max_file_edit_bytes:
            raise ToolCommandError(f"写入内容超过限制: {self.config.max_file_edit_bytes} bytes")
        old_text = target.read_text(encoding="utf-8") if target.exists() else ""
        target_state = self._file_state(target)
        diff = "".join(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=str(target.relative_to(self.workspace.root)) if target.exists() else "/dev/null",
                tofile=str(target.relative_to(self.workspace.root)),
            )
        )
        edit_id = f"edit-{uuid.uuid4().hex[:8]}"
        self.pending_file_edits[edit_id] = {
            "command": terminal_command.command,
            "argv": terminal_command.argv,
            "path": target,
            "content": content,
            "diff": diff,
            "target_state": target_state,
        }
        return {
            "edit_id": edit_id,
            "command": terminal_command.command,
            "path": str(target.relative_to(self.workspace.root)),
            "diff": diff,
            "old_lines": len(old_text.splitlines()),
            "new_lines": len(content.splitlines()),
        }

    def commit_file_edit(self, edit_id: str) -> dict:
        edit = self.pending_file_edits.get(edit_id)
        if not edit:
            raise ToolCommandError(f"未找到待执行文件修改: {edit_id}")
        target = edit["path"]
        if self._file_state(target) != edit["target_state"]:
            raise ToolCommandError("目标文件已在确认前发生变化，请重新生成修改。")
        checkpoint_id = self._create_checkpoint(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_text(target, edit["content"])
        self.pending_file_edits.pop(edit_id, None)
        self._prune_checkpoints()
        return {
            "command": edit["command"],
            "ok": True,
            "cwd": self._relative_cwd(),
            "stdout": f"已写入 {target.relative_to(self.workspace.root)}，checkpoint: {checkpoint_id}",
            "stderr": "",
            "returncode": 0,
            "checkpoint_id": checkpoint_id,
        }

    def discard_file_edit(self, edit_id: str) -> None:
        self.pending_file_edits.pop(edit_id, None)

    def _file_state(self, target: Path) -> dict:
        if not target.exists():
            return {"exists": False}
        if not target.is_file():
            raise WorkspaceError(f"不是普通文件: {target.relative_to(self.workspace.root)}")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        return {"exists": True, "digest": digest}

    def _atomic_write_text(self, target: Path, content: str) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".cheapclaw.tmp", dir=target.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            temp_path.replace(target)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def cleanup_checkpoints(self) -> None:
        if self.checkpoint_root.exists():
            shutil.rmtree(self.checkpoint_root)

    def _execute_file_builtin(self, command: TerminalCommand) -> dict:
        return {
            "command": command.command,
            "ok": False,
            "cwd": self._relative_cwd(),
            "error": "file replace 需要先生成内容并经用户确认后执行",
        }

    def _execute_checkpoint_builtin(self, command: TerminalCommand) -> dict:
        if command.argv[1] == "list":
            return self._list_checkpoints(command)
        if command.argv[1] == "restore":
            return self._restore_checkpoint(command)
        raise ToolCommandError("未知 checkpoint 命令")

    def _list_checkpoints(self, command: TerminalCommand) -> dict:
        checkpoints = []
        for manifest_path in sorted(self.checkpoint_root.glob("*/manifest.json")) if self.checkpoint_root.exists() else []:
            try:
                checkpoints.append(json.loads(manifest_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return {
            "command": command.command,
            "ok": True,
            "cwd": self._relative_cwd(),
            "stdout": json.dumps(checkpoints, ensure_ascii=False, indent=2),
            "stderr": "",
            "returncode": 0,
        }

    def _restore_checkpoint(self, command: TerminalCommand) -> dict:
        checkpoint_id = command.argv[2]
        manifest_path = self.checkpoint_root / checkpoint_id / "manifest.json"
        if not manifest_path.exists():
            raise ToolCommandError(f"checkpoint 不存在: {checkpoint_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        target = self.workspace.resolve(manifest["path"])
        backup = self.checkpoint_root / checkpoint_id / "original"
        if manifest.get("existed", True):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
        elif target.exists():
            target.unlink()
        return {
            "command": command.command,
            "ok": True,
            "cwd": self._relative_cwd(),
            "stdout": f"已恢复 checkpoint {checkpoint_id}: {manifest['path']}",
            "stderr": "",
            "returncode": 0,
        }

    def _create_checkpoint(self, target: Path) -> str:
        checkpoint_id = f"ckpt-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        checkpoint_dir = self.checkpoint_root / checkpoint_id
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        if existed:
            shutil.copy2(target, checkpoint_dir / "original")
        manifest = {
            "id": checkpoint_id,
            "path": str(target.relative_to(self.workspace.root)),
            "created_at": time.time(),
            "existed": existed,
        }
        (checkpoint_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return checkpoint_id

    def _prune_checkpoints(self) -> None:
        if not self.checkpoint_root.exists():
            return
        checkpoint_dirs = sorted(
            [path for path in self.checkpoint_root.iterdir() if path.is_dir()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for stale in checkpoint_dirs[self.config.checkpoint_keep_limit:]:
            shutil.rmtree(stale, ignore_errors=True)

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
