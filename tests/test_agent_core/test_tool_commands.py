import subprocess

import pytest

from cheapclaw.agent_core.config import ToolConfig
from cheapclaw.agent_core.tool_commands import (
    ControlledTerminalRunner,
    TerminalCommandPolicy,
    ToolCommandError,
)
from cheapclaw.agent_core.workspace import Workspace


def test_terminal_command_policy_normalizes_command_paths_and_quoting():
    policy = TerminalCommandPolicy(blacklist=(), whitelist=("ls",))

    command = policy.validate_command("/bin/ls 'some dir'")

    assert command.argv == ["ls", "some dir"]
    assert command.command == "ls 'some dir'"


@pytest.mark.parametrize(
    "command",
    [
        "rm file.txt",
        "ls $HOME",
        "FOO=bar ls",
        "ls `pwd`",
        "ls > out.txt",
        "ls && echo done",
    ],
)
def test_terminal_command_policy_rejects_blacklist_shell_ops_and_expansion(command):
    policy = TerminalCommandPolicy(blacklist=("rm",), whitelist=("ls",))

    with pytest.raises(ToolCommandError):
        policy.validate_command(command)


def test_terminal_command_policy_allows_safe_pipeline():
    policy = TerminalCommandPolicy(
        blacklist=("rm",), whitelist=("ls", "find", "cat", "grep", "head")
    )

    cmd = policy.validate_command("find . -name '*.py' | head -20")
    assert cmd.is_pipeline is True
    assert cmd.pipeline_segments == [
        ["find", ".", "-name", "*.py"],
        ["head", "-20"],
    ]
    assert policy.requires_confirmation(cmd) is True


def test_terminal_command_policy_rejects_dangerous_pipeline():
    policy = TerminalCommandPolicy(
        blacklist=("rm",), whitelist=("ls", "find", "cat", "grep")
    )

    # 管线中的命令不在白名单: bash
    with pytest.raises(ToolCommandError, match="不在白名单"):
        policy.validate_command("find . | bash")

    # 管线末端不是安全消费者: cat
    with pytest.raises(ToolCommandError, match="只读消费者"):
        policy.validate_command("find . | cat")

    # 重定向仍然被拦截
    with pytest.raises(ToolCommandError, match="不允许 shell 操作符"):
        policy.validate_command("find . > /tmp/out")

    # 后台执行仍然被拦截
    with pytest.raises(ToolCommandError, match="不允许 shell 操作符"):
        policy.validate_command("find . &")


def test_terminal_command_policy_rejects_pipeline_with_env_var():
    policy = TerminalCommandPolicy(blacklist=("rm",), whitelist=("ls", "find", "cat"))

    with pytest.raises(ToolCommandError, match="环境变量赋值"):
        policy.validate_command("FOO=bar find . | head")


def test_terminal_command_policy_multi_stage_pipeline():
    policy = TerminalCommandPolicy(whitelist=("find", "grep", "sort", "head", "wc"))

    cmd = policy.validate_command("find . -name '*.py' | grep test | wc -l")
    assert cmd.is_pipeline is True
    assert len(cmd.pipeline_segments) == 3
    assert cmd.pipeline_segments[0] == ["find", ".", "-name", "*.py"]
    assert cmd.pipeline_segments[1] == ["grep", "test"]
    assert cmd.pipeline_segments[2] == ["wc", "-l"]


def test_terminal_command_policy_pipeline_syntax_errors():
    policy = TerminalCommandPolicy(whitelist=("find", "head"))

    with pytest.raises(ToolCommandError, match="\\| 前缺少命令"):
        policy.validate_command("| find .")

    with pytest.raises(ToolCommandError, match="\\| 后缺少命令"):
        policy.validate_command("find . |")


def test_terminal_command_policy_validates_builtins():
    policy = TerminalCommandPolicy(blacklist=(), whitelist=("ls",))

    assert policy.validate_command("file replace src/app.py").argv == [
        "file",
        "replace",
        "src/app.py",
    ]
    assert policy.validate_command("checkpoint list").argv == ["checkpoint", "list"]
    assert policy.validate_command("checkpoint restore ckpt-1").argv == [
        "checkpoint",
        "restore",
        "ckpt-1",
    ]

    for command in [
        "file read src/app.py",
        "file replace *.py",
        "checkpoint delete ckpt-1",
    ]:
        with pytest.raises(ToolCommandError):
            policy.validate_command(command)


def test_terminal_command_policy_confirmation_requirements_and_shell_detection():
    policy = TerminalCommandPolicy(blacklist=("rm",), whitelist=("ls", "cd"))

    assert not policy.requires_confirmation(policy.validate_command("ls"))
    assert policy.requires_confirmation(policy.validate_command("python script.py"))
    assert policy.requires_confirmation(policy.validate_command("file replace a.txt"))
    assert policy.requires_confirmation(
        policy.validate_command("checkpoint restore ckpt-1")
    )
    assert policy.requires_confirmation(policy.validate_command("checkpoint list"))

    assert policy.looks_like_shell_command("ls -la")
    assert policy.looks_like_shell_command("FOO=bar python")
    assert policy.looks_like_shell_command("./script")
    assert not policy.looks_like_shell_command("普通说明")


def test_runner_parse_planner_reply_final_command_prose_and_rejected_shell(tmp_path):
    runner = ControlledTerminalRunner(Workspace(tmp_path))

    assert runner.parse_planner_reply("final: done") == {
        "action": "final",
        "answer": "done",
        "explicit_final": True,
    }
    assert runner.parse_planner_reply("ls") == {
        "action": "command",
        "command": "ls",
        "argv": ["ls"],
        "is_pipeline": False,
        "pipeline_segments": None,
    }
    assert runner.parse_planner_reply("This is ordinary prose.") == {
        "action": "command",
        "command": "This is ordinary prose.",
        "argv": ["This", "is", "ordinary", "prose."],
        "is_pipeline": False,
        "pipeline_segments": None,
    }
    assert runner.parse_planner_reply("普通说明")["action"] == "final"

    with pytest.raises(ToolCommandError):
        runner.parse_planner_reply("rm -rf .")


def test_runner_safe_cd_inside_workspace(tmp_path):
    (tmp_path / "src").mkdir()
    runner = ControlledTerminalRunner(Workspace(tmp_path))

    result = runner.execute({"command": "cd src", "argv": ["cd", "src"]})

    assert result["ok"] is True
    assert result["cwd"] == "src"
    assert runner.cwd == tmp_path / "src"

    outside = runner.execute({"command": "cd ..", "argv": ["cd", ".."]})
    assert outside["ok"] is True
    assert outside["cwd"] == "."

    rejected = runner.execute({"command": "cd ..", "argv": ["cd", ".."]})
    assert rejected["ok"] is False
    assert "workspace" in rejected["error"]


def test_runner_subprocess_uses_safe_invocation(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, cwd, text, capture_output, timeout, shell):
        calls.append(
            {
                "argv": argv,
                "cwd": cwd,
                "text": text,
                "capture_output": capture_output,
                "timeout": timeout,
                "shell": shell,
            }
        )
        return subprocess.CompletedProcess(argv, 0, "out", "err")

    monkeypatch.setattr("cheapclaw.agent_core.tool_commands.subprocess.run", fake_run)
    runner = ControlledTerminalRunner(
        Workspace(tmp_path),
        config=ToolConfig(subprocess_timeout_seconds=7),
        policy=TerminalCommandPolicy(blacklist=(), whitelist=("python",)),
    )

    result = runner.execute(
        {"command": "python --version", "argv": ["python", "--version"]}
    )

    assert result["ok"] is True
    assert result["stdout"] == "out"
    assert result["stderr"] == "err"
    assert calls == [
        {
            "argv": ["python", "--version"],
            "cwd": str(tmp_path),
            "text": True,
            "capture_output": True,
            "timeout": 7,
            "shell": False,
        }
    ]


def test_runner_timeout_and_os_errors_become_observations(monkeypatch, tmp_path):
    runner = ControlledTerminalRunner(
        Workspace(tmp_path),
        policy=TerminalCommandPolicy(blacklist=(), whitelist=("python",)),
    )

    def timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(
        "cheapclaw.agent_core.tool_commands.subprocess.run", timeout_run
    )
    timeout = runner.execute(
        {"command": "python slow.py", "argv": ["python", "slow.py"]}
    )
    assert timeout["ok"] is False
    assert "timed out" in timeout["error"]

    def os_error_run(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(
        "cheapclaw.agent_core.tool_commands.subprocess.run", os_error_run
    )
    errored = runner.execute(
        {"command": "python fail.py", "argv": ["python", "fail.py"]}
    )
    assert errored["ok"] is False
    assert "boom" in errored["error"]


def test_runner_observation_truncation(tmp_path):
    runner = ControlledTerminalRunner(
        Workspace(tmp_path), config=ToolConfig(observation_text_limit=3)
    )

    truncated = runner.truncate_observation({"stdout": "abcdef", "stderr": "12345"})

    assert truncated["stdout"] == "abc\n...[truncated]"
    assert truncated["stderr"] == "123\n...[truncated]"
    assert truncated["truncated_stdout_chars"] == 3
    assert truncated["truncated_stderr_chars"] == 2


def test_runner_file_edit_prepare_commit_and_discard(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("old\n", encoding="utf-8")
    runner = ControlledTerminalRunner(Workspace(tmp_path))

    prepared = runner.prepare_file_replace("file replace file.txt", "new\n")

    assert prepared["path"] == "file.txt"
    assert "-old" in prepared["diff"]
    assert "+new" in prepared["diff"]
    assert target.read_text(encoding="utf-8") == "old\n"

    result = runner.commit_file_edit(prepared["edit_id"])
    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "new\n"
    assert result["checkpoint_id"].startswith("ckpt-")

    discarded = runner.prepare_file_replace("file replace file.txt", "discarded\n")
    runner.discard_file_edit(discarded["edit_id"])
    assert target.read_text(encoding="utf-8") == "new\n"
    with pytest.raises(ToolCommandError):
        runner.commit_file_edit(discarded["edit_id"])


def test_runner_checkpoint_create_list_restore_and_cleanup(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("original\n", encoding="utf-8")
    runner = ControlledTerminalRunner(Workspace(tmp_path))

    prepared = runner.prepare_file_replace("file replace file.txt", "changed\n")
    committed = runner.commit_file_edit(prepared["edit_id"])
    assert target.read_text(encoding="utf-8") == "changed\n"

    listed = runner.execute(
        {"command": "checkpoint list", "argv": ["checkpoint", "list"]}
    )
    assert listed["ok"] is True
    assert committed["checkpoint_id"] in listed["stdout"]

    restored = runner.execute(
        {
            "command": f"checkpoint restore {committed['checkpoint_id']}",
            "argv": ["checkpoint", "restore", committed["checkpoint_id"]],
        }
    )
    assert restored["ok"] is True
    assert target.read_text(encoding="utf-8") == "original\n"

    runner.cleanup_checkpoints()
    assert not runner.checkpoint_root.exists()


def test_runner_rejects_commit_when_target_changes_before_confirmation(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("old\n", encoding="utf-8")
    runner = ControlledTerminalRunner(Workspace(tmp_path))
    prepared = runner.prepare_file_replace("file replace file.txt", "new\n")

    target.write_text("changed elsewhere\n", encoding="utf-8")

    with pytest.raises(ToolCommandError, match="目标文件已在确认前发生变化"):
        runner.commit_file_edit(prepared["edit_id"])
    assert target.read_text(encoding="utf-8") == "changed elsewhere\n"


def test_runner_checkpoint_restore_removes_file_created_after_checkpoint(tmp_path):
    target = tmp_path / "new.txt"
    runner = ControlledTerminalRunner(Workspace(tmp_path))

    prepared = runner.prepare_file_replace("file replace new.txt", "created\n")
    committed = runner.commit_file_edit(prepared["edit_id"])
    assert target.exists()

    restored = runner.execute(
        {
            "command": f"checkpoint restore {committed['checkpoint_id']}",
            "argv": ["checkpoint", "restore", committed["checkpoint_id"]],
        }
    )

    assert restored["ok"] is True
    assert not target.exists()


def test_runner_execute_safe_pipeline(tmp_path):
    """集成测试：安全的管线命令通过 Python 级 Popen 链式执行。"""
    target = tmp_path / "a.py"
    target.write_text("hello\nworld\n", encoding="utf-8")

    runner = ControlledTerminalRunner(
        Workspace(tmp_path),
        policy=TerminalCommandPolicy(
            blacklist=("rm",),
            whitelist=("find", "cat", "head", "wc", "grep"),
        ),
    )
    cmd = runner.policy.validate_command(f"cat {target} | wc -l")
    assert cmd.is_pipeline is True

    action = {
        "command": cmd.command,
        "argv": cmd.argv,
        "is_pipeline": cmd.is_pipeline,
        "pipeline_segments": cmd.pipeline_segments,
    }
    result = runner.execute(action)
    assert result["ok"] is True
    assert "2" in result["stdout"]


def test_runner_pipeline_requires_confirmation(tmp_path):
    runner = ControlledTerminalRunner(
        Workspace(tmp_path),
        policy=TerminalCommandPolicy(
            blacklist=("rm",),
            whitelist=("find", "cat", "head", "wc"),
        ),
    )
    cmd = runner.policy.validate_command("find . -name '*.py' | head -5")
    assert runner.requires_confirmation(cmd) is True
