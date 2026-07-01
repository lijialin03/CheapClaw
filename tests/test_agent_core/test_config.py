import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cheapclaw.agent_core.config import (
    AgentConfig,
    AppConfig,
    BrowserConfig,
    MemoryConfig,
    ToolConfig,
    load_config,
)


def test_config_models_instantiate_with_defaults():
    assert isinstance(AppConfig(), AppConfig)
    assert isinstance(AgentConfig(), AgentConfig)
    assert isinstance(BrowserConfig(), BrowserConfig)
    assert isinstance(MemoryConfig(), MemoryConfig)
    assert isinstance(ToolConfig(), ToolConfig)


def test_positive_integer_fields_reject_non_positive_values():
    with pytest.raises(ValidationError):
        ToolConfig(observation_text_limit=0)


@pytest.mark.parametrize(
    "field",
    [
        "command_blacklist",
        "command_whitelist",
        "confirm_command_replies",
        "tool_router_positive_replies",
        "tool_router_negative_replies",
        "router_sentinel_replies",
        "workspace_read_verbs",
        "workspace_targets",
    ],
)
def test_tool_config_tuple_fields_reject_empty_strings(field):
    with pytest.raises(ValidationError):
        ToolConfig(**{field: ("",)})


def test_memory_config_rejects_empty_generic_short_queries():
    with pytest.raises(ValidationError):
        MemoryConfig(generic_short_queries=())


def test_load_config_applies_overrides_from_file(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agent": {
                    "max_text_chars": 1234,
                    "max_tool_steps": 2,
                    "tools": {
                        "observation_text_limit": 99,
                        "command_whitelist": ["pwd"],
                    },
                },
                "browser": {
                    "headless": False,
                    "timeout": 5000,
                    "storage_state_path": str(tmp_path / "state.json"),
                },
                "memory": {
                    "context_budget": 321,
                    "generic_short_queries": ["继续"],
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.agent.max_text_chars == 1234
    assert config.agent.max_tool_steps == 2
    assert config.agent.tools.observation_text_limit == 99
    assert config.agent.tools.command_whitelist == ("pwd",)
    assert config.browser.headless is False
    assert config.browser.timeout == 5000
    assert config.browser.storage_state_path == tmp_path / "state.json"
    assert config.memory.context_budget == 321
    assert config.memory.generic_short_queries == ("继续",)


def test_load_config_nonexistent_path_falls_back_to_defaults(tmp_path):
    assert load_config(tmp_path / "missing.json") == AppConfig()


def test_workspace_default_config_takes_precedence(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "default_config.json").write_text(
        json.dumps({"agent": {"max_text_chars": 4321}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.agent.max_text_chars == 4321


def test_load_config_uses_package_default_when_workspace_config_missing(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert "写入" in config.agent.tools.workspace_read_verbs
    assert "replace" in config.agent.tools.workspace_read_verbs
    assert "index.html" in config.agent.tools.workspace_targets
    assert ".html" in config.agent.tools.workspace_targets
    assert "cheapclaw/agent_core" in config.agent.tools.workspace_targets
    assert "agent_core" not in config.agent.tools.workspace_targets
    assert "当前仓库" not in config.agent.tools.workspace_read_verbs


def test_default_config_file_contains_high_confidence_workspace_fallback_terms():
    config = load_config()

    assert "写入" in config.agent.tools.workspace_read_verbs
    assert "replace" in config.agent.tools.workspace_read_verbs
    assert "index.html" in config.agent.tools.workspace_targets
    assert ".html" in config.agent.tools.workspace_targets
    assert "cheapclaw/agent_core" in config.agent.tools.workspace_targets
    assert "agent_core" not in config.agent.tools.workspace_targets
    assert "当前仓库" not in config.agent.tools.workspace_read_verbs
