import pytest

from cheapclaw.agent_core.prompt_loader import (
    PLACEHOLDER_PATTERN,
    load_prompt_template,
    render_prompt,
)


@pytest.mark.parametrize(
    "name, marker",
    [
        ("chat.md", "{{ memory_section }}"),
        ("tool_router.md", "{{ recent_context }}"),
        ("tool_router_retry.md", "{{ invalid_reply }}"),
        ("tool_planner.md", "{{ observations_json }}"),
        ("memory_summary.md", "{{ text }}"),
        ("file_replace.md", "{{ path }}"),
        ("file_transport.md", "当前用户输入"),
    ],
)
def test_known_prompt_templates_load_successfully(name, marker):
    template = load_prompt_template(name)

    assert marker in template


@pytest.mark.parametrize(
    "name", ["missing.md", "../config/default_config.json", "/etc/passwd"]
)
def test_missing_or_path_escape_prompt_names_fail_safely(name):
    with pytest.raises(FileNotFoundError):
        load_prompt_template(name)


def test_render_prompt_replaces_variables():
    rendered = render_prompt(
        "chat.md", memory_section="MEMORY HERE", user_input="USER HERE"
    )

    assert "MEMORY HERE" in rendered
    assert "USER HERE" in rendered
    assert "{{" not in rendered


def test_render_prompt_missing_variables_raise_key_error():
    with pytest.raises(KeyError):
        render_prompt("chat.md", memory_section="MEMORY ONLY")


@pytest.mark.parametrize(
    "name, expected_placeholders",
    [
        ("chat.md", {"memory_section", "user_input"}),
        ("tool_router.md", {"user_input", "recent_context"}),
        (
            "tool_router_retry.md",
            {"user_input", "recent_context", "invalid_reply"},
        ),
        (
            "tool_planner.md",
            {
                "user_input",
                "recent_context",
                "examples",
                "policy",
                "force_final_rule",
                "observations_json",
            },
        ),
        ("memory_summary.md", {"text"}),
        (
            "file_replace.md",
            {"user_input", "recent_context", "path", "observations_json"},
        ),
        ("file_transport.md", set()),
    ],
)
def test_prompt_template_placeholders_match_expected_contract(
    name, expected_placeholders
):
    template = load_prompt_template(name)

    assert set(PLACEHOLDER_PATTERN.findall(template)) == expected_placeholders


def test_tool_router_prompt_classifies_local_file_capabilities_as_terminal():
    template = load_prompt_template("tool_router.md")

    assert "本地工具" in template
    assert "你可以读取我的本地文件" in template
    assert "当前仓库" in template
    assert "请直接修改 index.html 文件" in template
    assert "terminal" in template
    assert "解释一下 flex 布局" in template
    assert "chat" in template


def test_tool_planner_prompt_requires_current_read_before_file_replace():
    template = load_prompt_template("tool_planner.md")

    assert "当前工具编排" in template
    assert "用 cat 读取目标文件" in template
    assert "不要直接 file replace" in template
    assert "近期上下文不能替代当前文件观察" in template
    assert "file replace <path>" in template


def test_file_replace_prompt_requires_localized_changes():
    template = load_prompt_template("file_replace.md")

    assert "近期对话上下文" in template
    assert "不能把近期上下文当成当前文件内容" in template
    assert "只修改用户要求相关的部分" in template
    assert "不要删除与请求无关" in template


def test_chat_prompt_does_not_deny_local_file_capability():
    template = load_prompt_template("chat.md")

    assert "受控本地工具" in template
    assert "用户授权和提供路径后处理" in template
    assert "提供路径" in template
    assert "无法访问本地文件" not in template
