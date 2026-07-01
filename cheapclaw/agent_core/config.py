import json
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_CONFIG_PACKAGE = "cheapclaw.config"
DEFAULT_CONFIG_RESOURCE = "default_config.json"

WORKSPACE_READ_VERBS = (
    "读取",
    "读",
    "查看",
    "检查",
    "列出",
    "看看",
    "打开",
    "分析",
    "总结",
    "修改",
    "改",
    "写入",
    "替换",
    "创建",
    "保存",
    "应用",
    "review",
    "analyze",
    "read",
    "show",
    "list",
    "stat",
    "edit",
    "modify",
    "write",
    "replace",
    "update",
    "create",
    "save",
    "apply",
)

WORKSPACE_TARGETS = (
    "目录",
    "文件",
    "路径",
    "当前目录",
    "本地文件",
    "本地目录",
    "workspace",
    "run.py",
    "index.html",
    "package.json",
    ".py",
    ".json",
    ".md",
    ".txt",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".vue",
    ".yaml",
    ".yml",
    "/",
    "./",
    "../",
    "cheapclaw",
    "cheapclaw/agent_core",
    "cheapclaw/ui",
    "cheapclaw/model_clients",
    "config",
)


class ToolConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    command_blacklist: tuple[str, ...] = ("rm",)
    command_whitelist: tuple[str, ...] = (
        "ls",
        "cd",
        "cat",
        "find",
        "grep",
        "head",
        "tail",
        "wc",
    )
    safe_pipe_consumers: tuple[str, ...] = (
        "head",
        "tail",
        "grep",
        "wc",
        "sort",
        "uniq",
        "cut",
        "tr",
        "sed",
    )
    observation_text_limit: int = Field(default=8000, gt=0)
    subprocess_timeout_seconds: int = Field(default=15, gt=0)
    max_file_edit_bytes: int = Field(default=1_000_000, gt=0)
    checkpoint_keep_limit: int = Field(default=20, gt=0)
    confirm_command_replies: tuple[str, ...] = ("y", "yes", "确认", "执行", "是")
    cancel_command_reply: str = "no"
    tool_router_positive_replies: tuple[str, ...] = ("terminal", "tool", "tools", "yes")
    tool_router_negative_replies: tuple[str, ...] = ("chat", "none", "no")
    router_sentinel_replies: tuple[str, ...] = (
        "chat",
        "none",
        "no",
        "terminal",
        "tool",
        "tools",
        "yes",
    )
    workspace_read_verbs: tuple[str, ...] = WORKSPACE_READ_VERBS
    workspace_targets: tuple[str, ...] = WORKSPACE_TARGETS
    tool_step_limit_message: str = "已达到终端命令调用步数上限，无法继续读取更多信息。"
    file_edit_diff_preview_chars: int = Field(default=3000, gt=0)

    @field_validator(
        "command_blacklist",
        "command_whitelist",
        "safe_pipe_consumers",
        "confirm_command_replies",
        "tool_router_positive_replies",
        "tool_router_negative_replies",
        "router_sentinel_replies",
        "workspace_read_verbs",
        "workspace_targets",
    )
    @classmethod
    def validate_non_empty_tuple_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value):
            raise ValueError("config lists must contain non-empty strings")
        return value

    @field_validator("cancel_command_reply", "tool_step_limit_message")
    @classmethod
    def validate_non_empty_string(cls, value: str) -> str:
        if not value:
            raise ValueError("config strings must be non-empty")
        return value


class AgentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_text_chars: int = Field(default=10000, gt=0)
    file_transport_enabled: bool = True
    tool_orchestration_enabled: bool = True
    max_tool_steps: int = Field(default=5, gt=0)
    upload_dir: Path | None = None
    tools: ToolConfig = ToolConfig()


class BrowserConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    headless: bool = False
    timeout: int = Field(default=120000, gt=0)
    storage_state_path: Path | None = None


class MemoryConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    context_budget: int = Field(default=4000, gt=0)
    key_info_context_budget: int = Field(default=600, gt=0)
    summary_context_budget: int = Field(default=900, gt=0)
    recent_context_budget: int = Field(default=1000, gt=0)
    max_selected_key_info: int = Field(default=8, gt=0)
    max_selected_summaries: int = Field(default=3, gt=0)
    auto_compress_threshold: float = Field(default=0.8, gt=0, le=1)
    auto_trim_threshold: float = Field(default=0.9, gt=0, le=1)
    min_messages_to_compress: int = Field(default=4, gt=0)
    recent_messages_to_keep: int = Field(default=2, gt=0)
    summary_message_char_limit: int = Field(default=600, gt=0)
    generic_short_queries: tuple[str, ...] = (
        "继续",
        "接着",
        "然后",
        "好的",
        "ok",
        "yes",
        "嗯",
        "好",
    )
    bm25_k1: float = Field(default=1.2, gt=0)
    bm25_b: float = Field(default=0.75, gt=0)
    buffer_max_tokens: int = Field(default=3000, gt=0)
    max_summaries: int = Field(default=20, gt=0)

    @field_validator("generic_short_queries")
    @classmethod
    def validate_generic_short_queries(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("generic_short_queries must be a non-empty list")
        if any(not query for query in value):
            raise ValueError("generic_short_queries must contain non-empty strings")
        return value


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent: AgentConfig = AgentConfig()
    browser: BrowserConfig = BrowserConfig(headless=True)
    memory: MemoryConfig = MemoryConfig()


def load_config(path: str | Path | None = None) -> AppConfig:
    if path is not None:
        config_path = Path(path)
        if not config_path.exists():
            return AppConfig()
        with open(config_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return AppConfig.model_validate(data)

    workspace_config_path = Path.cwd() / "config" / "default_config.json"
    if workspace_config_path.exists():
        with open(workspace_config_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return AppConfig.model_validate(data)

    config_text = (
        resources.files(DEFAULT_CONFIG_PACKAGE)
        .joinpath(DEFAULT_CONFIG_RESOURCE)
        .read_text(encoding="utf-8")
    )
    return AppConfig.model_validate(json.loads(config_text))
