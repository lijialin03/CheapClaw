from __future__ import annotations

import importlib.resources as _resources
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict


class SelectorConfig(BaseModel):
    """冻选择器配置，从 YAML 反序列化。"""

    model_config = ConfigDict(frozen=True)

    # 核心交互
    composer: str
    send_button: str
    sendable_fallback: str | None = None

    # 上传相关
    upload_button: str | None = None
    upload_menu_trigger: str | None = None
    upload_menu_item: str | None = None
    upload_file_input: str | None = None

    # 回复区域检测
    reply_content: str
    reply_citation: str | None = None
    user_message: str | None = None
    file_card: str | None = None

    # 对话管理 (DeepSeek)
    conversation_item_link: str | None = None
    conversation_menu_button: str | None = None
    conversation_delete_option: str | None = None
    confirm_dialog_delete_button: str | None = None

    # 生成状态关键词
    generation_stop_keywords: list[str] = []

    # 登录检测
    login_url_keywords: list[str] = []

    # UI 辅助 (Qwen)
    guidance_close_button: str | None = None

    # 上传后文件卡片相关
    upload_file_card_list: str | None = None
    upload_file_card_item: str | None = None
    upload_file_card_name: str | None = None
    upload_file_card_ext: str | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> SelectorConfig:
        with open(path, "r", encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f)
        return cls(**data)


def load_selector_config(name: str) -> SelectorConfig:
    """从 cheapclaw/model_clients/<name>/selectors.yaml 加载选择器配置。"""
    package = f"cheapclaw.model_clients.{name}"
    path = (
        Path(str(_resources.files(package))) / "selectors.yaml"  # type: ignore[arg-type]
    )
    if not path.exists():
        raise FileNotFoundError(f"选择器配置文件不存在: {path}")
    return SelectorConfig.from_yaml(path)
