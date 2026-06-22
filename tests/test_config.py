import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_core.config import (
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
