import pytest

from agent_core.prompt_loader import PLACEHOLDER_PATTERN, load_prompt_template, render_prompt


@pytest.mark.parametrize(
    "name, marker",
    [
        ("chat.md", "{{ memory_section }}"),
        ("tool_router.md", "{{ user_input }}"),
        ("tool_planner.md", "{{ observations_json }}"),
        ("memory_summary.md", "{{ text }}"),
        ("file_replace.md", "{{ path }}"),
        ("file_transport.md", "当前用户输入"),
    ],
)
def test_known_prompt_templates_load_successfully(name, marker):
    template = load_prompt_template(name)

    assert marker in template


@pytest.mark.parametrize("name", ["missing.md", "../config/default_config.json", "/etc/passwd"])
def test_missing_or_path_escape_prompt_names_fail_safely(name):
    with pytest.raises(FileNotFoundError):
        load_prompt_template(name)


def test_render_prompt_replaces_variables():
    rendered = render_prompt("chat.md", memory_section="MEMORY HERE", user_input="USER HERE")

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
        ("tool_router.md", {"user_input"}),
        ("tool_planner.md", {"user_input", "examples", "policy", "force_final_rule", "observations_json"}),
        ("memory_summary.md", {"text"}),
        ("file_replace.md", {"user_input", "path", "observations_json"}),
        ("file_transport.md", set()),
    ],
)
def test_prompt_template_placeholders_match_expected_contract(name, expected_placeholders):
    template = load_prompt_template(name)

    assert set(PLACEHOLDER_PATTERN.findall(template)) == expected_placeholders
