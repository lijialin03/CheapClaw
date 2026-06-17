# qwen.py
from pathlib import Path

from agent_core.config import BrowserConfig

from .assets import load_asset_text
from .browser_base import BrowserFrontendAdapter, BrowserModelClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
STORAGE_STATE_PATH = CONFIG_DIR / "storage_state.json"

BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
]


class QwenAdapter(BrowserFrontendAdapter):
    """Qwen-specific DOM selectors and browser interactions."""
    def default_url(self) -> str:
        return "https://chat.qwen.ai/"

    def wait_until_ready(self) -> None:
        self.page.wait_for_selector(".message-input-textarea", timeout=self.config.timeout)
        self.page.wait_for_timeout(1000)

    def after_page_loaded(self) -> None:
        self._inject_overlay_auto_dismiss()

    def latest_reply_text(self) -> str:
        try:
            reply_locator = self.page.locator(".qwen-chat-message-assistant .response-message-content")
            if reply_locator.count() == 0:
                return ""
            return reply_locator.last.inner_text(timeout=1000).strip()
        except Exception:
            return ""

    def is_reply_complete(self) -> bool:
        try:
            return bool(self.page.evaluate(load_asset_text("qwen/scripts/reply_complete.js")))
        except Exception:
            return False

    def is_generation_in_progress(self) -> bool:
        try:
            return bool(self.page.evaluate(load_asset_text("qwen/scripts/generation_in_progress.js")))
        except Exception:
            return False

    def assistant_message_count(self) -> int | None:
        return self.page.locator(".response-message-content").count()

    def try_handle_reply_preference_ui(self) -> str:
        """处理 Qwen 偶发的“您更喜欢哪个回复”双回复评测 UI，默认选择第一个回复。"""
        try:
            if self.page.locator("text=您更喜欢哪个回复").count() == 0:
                return ""

            self.logger.warning("检测到双回复评测 UI，默认选择第一个回复")
            self.logger.screenshot("reply_preference_ui", full_page=True)
            result = self.page.evaluate(load_asset_text("qwen/scripts/reply_preference.js"))
            reply = (result or {}).get("reply", "").strip()
            if not (result or {}).get("clicked"):
                self.logger.warning("未能点击第一个偏好回复按钮")
            self.page.wait_for_timeout(1000)
            return reply
        except Exception as e:
            self.logger.debug(f"处理双回复评测 UI 失败: {type(e).__name__}: {e}")
            return ""

    def before_text_send(self, text: str, auto_remove_limit: bool = True, auto_close_guidance: bool = True):
        textarea = self.page.locator(".message-input-textarea")
        textarea.wait_for(state="visible", timeout=self.config.timeout)

        if auto_close_guidance:
            self._try_close_guidance()
            textarea = self.page.locator(".message-input-textarea")
            textarea.wait_for(state="visible", timeout=self.config.timeout)

        if auto_remove_limit:
            self.page.evaluate(
                """() => {
                    const textarea = document.querySelector('.message-input-textarea');
                    if (textarea) textarea.removeAttribute('maxlength');
                }"""
            )

        textarea.click()
        textarea.fill(text)
        self.page.wait_for_timeout(300)
        return {
            "previous_user_message_count": self._user_message_count(),
            "previous_assistant_message_count": self.assistant_message_count(),
        }

    def before_file_send(self, file_path: Path, prompt: str = None):
        self.logger.debug(f"准备上传文件: {file_path}")
        self._try_close_guidance()
        return {
            "previous_user_message_count": self._user_message_count(),
            "previous_assistant_message_count": self.assistant_message_count(),
            "prefer_button": True,
            "had_text": bool(prompt),
        }

    def fill_prompt_after_upload(self, prompt: str):
        textarea = self.page.locator(".message-input-textarea")
        textarea.wait_for(state="visible", timeout=self.config.timeout)
        textarea.fill(prompt)
        return {"had_text": True}

    def upload_file(self, file_path: Path) -> None:
        """按 Qwen 前端真实交互打开上传菜单并选择文件，失败时回退隐藏 input。"""
        try:
            self._open_upload_menu()
            upload_item = self._upload_attachment_item()
            upload_item.wait_for(state="visible", timeout=5000)
            self.logger.screenshot("send_file_upload_menu_opened", full_page=False)
            with self.page.expect_file_chooser(timeout=5000) as chooser_info:
                upload_item.click(timeout=5000)
            chooser_info.value.set_files(str(file_path))
        except Exception as e:
            self.logger.debug(f"上传菜单方式失败，回退隐藏 input: {type(e).__name__}: {e}")
            self.logger.screenshot("send_file_upload_menu_failed", full_page=False)
            self._upload_file_by_input(file_path)

        self._wait_for_uploaded_file_card(file_path)
        self.logger.info("文件已显示在输入框附件卡片中")
        self.logger.screenshot("send_file_after_upload_card", full_page=False)

    def _open_upload_menu(self) -> None:
        """点击输入框左侧加号，打开包含“上传附件”的菜单。"""
        plus_button = self.page.locator(
            ".mode-select .ant-dropdown-trigger, .mode-select-open, #notification_update_popover_mode_select"
        ).first
        plus_button.wait_for(state="visible", timeout=5000)
        plus_button.click(timeout=5000)

    def _upload_attachment_item(self):
        """返回上传附件菜单项；该菜单可能挂载在 body 弹层中。"""
        return self.page.locator(
            "text=上传附件"
        ).first

    def _upload_file_by_input(self, file_path: Path) -> None:
        file_input = self.page.locator("#filesUpload")
        file_input.wait_for(state="attached", timeout=self.config.timeout)
        file_input.set_input_files(str(file_path))

    def _wait_for_uploaded_file_card(self, file_path: Path) -> None:
        """等待上传后的文件卡片出现在输入框附件区域。"""
        stem = file_path.stem
        suffix = file_path.suffix
        self.page.wait_for_selector(
            ".message-input-column-file .file-card-list .fileitem-btn",
            state="visible",
            timeout=self.config.timeout,
        )
        self.page.wait_for_function(
            """({stem, suffix}) => {
                const cards = Array.from(document.querySelectorAll('.message-input-column-file .file-card-list .fileitem-btn'));
                return cards.some((card) => {
                    const name = card.querySelector('.fileitem-file-name-text')?.textContent?.trim() || '';
                    const ext = card.querySelector('.fileitem-file-name-ext')?.textContent?.trim() || '';
                    const fullName = `${name}${ext}`;
                    return (name === stem && ext === suffix) || fullName === `${stem}${suffix}`;
                });
            }""",
            arg={"stem": stem, "suffix": suffix},
            timeout=self.config.timeout,
        )

    def wait_until_sendable(self, timeout: int = None) -> None:
        """等待发送按钮可用，表示当前输入或附件可以提交。"""
        wait_timeout = timeout or self.config.timeout
        self.page.wait_for_selector(
            "button.send-button:not([disabled]), .send-button:not([disabled])",
            state="visible",
            timeout=wait_timeout,
        )

    def _user_message_count(self) -> int:
        """返回当前页面中的用户消息数量。"""
        return self.page.locator(".qwen-chat-message-user").count()

    def send_current_message(
        self,
        previous_user_message_count: int = None,
        prefer_button: bool = False,
        had_text: bool = True,
    ):
        textarea = self.page.locator(".message-input-textarea")
        send_button = self.page.locator("button.send-button:not([disabled]), .send-button:not([disabled])").first

        if prefer_button:
            send_attempts = ("button", "enter")
        else:
            send_attempts = ("enter", "button")

        for method in send_attempts:
            try:
                if method == "enter":
                    textarea.press("Enter")
                else:
                    self.wait_until_sendable(timeout=5000)
                    send_button.click(timeout=5000)
                if self._is_sent(
                    previous_user_message_count=previous_user_message_count,
                    had_text=had_text,
                ):
                    return
            except Exception as e:
                self.logger.debug(f"{method} 发送异常: {type(e).__name__}: {e}")

        raise RuntimeError("发送失败：未检测到新用户消息或发送完成状态")

    def _is_sent(self, previous_user_message_count: int = None, had_text: bool = True) -> bool:
        """优先通过用户消息数量增加判断发送成功，文本消息可回退到输入框清空。"""
        if previous_user_message_count is not None:
            try:
                self.page.wait_for_function(
                    """(previousCount) => document.querySelectorAll('.qwen-chat-message-user').length > previousCount""",
                    arg=previous_user_message_count,
                    timeout=8000,
                )
                return True
            except Exception:
                pass

        if not had_text:
            return False

        try:
            self.page.wait_for_function(
                """() => {
                    const textarea = document.querySelector('.message-input-textarea');
                    return textarea && textarea.value === '';
                }""",
                timeout=5000,
            )
            return True
        except Exception:
            return False

    def _try_close_guidance(self) -> bool:
        """如果当前有首页引导/示例区域，尝试关闭它。"""
        try:
            close_button = self.page.locator(
                "button.guidance-pc-close-btn, button.close-button, .guidance-pc-close-btn, .close-button"
            ).first
            if not close_button.is_visible(timeout=1000):
                return False
            close_button.click(timeout=5000)
            self.page.wait_for_timeout(1000)
            return True
        except Exception:
            return False

    def is_logged_in(self) -> bool:
        """检查当前页面是否已登录。"""
        try:
            current_url = self.page.url.lower()
            if any(keyword in current_url for keyword in ["passport", "login", "auth"]):
                self.logger.warning(f"检测到重定向至登录页: {current_url}")
                return False

            state = self.page.evaluate(load_asset_text("qwen/scripts/login_state.js"))
            self.logger.debug(f"登录状态检查: {state}")
            return state.get("authStatus") == 200 and not state.get("authButtons")
        except Exception as e:
            self.logger.warning(f"登录状态检查失败: {type(e).__name__}: {e}")
            return False

    def _inject_overlay_auto_dismiss(self):
        """自动关闭可能遮挡输入区的弹窗。"""
        self.page.evaluate(load_asset_text("qwen/scripts/overlay_auto_dismiss.js"))
        self.logger.debug("弹窗自动关闭 hook 已注入")


class QwenClient(BrowserModelClient):
    """
    通义千问网页版自动化客户端（基于持久化会话）。
    首次使用时需手动登录一次保存状态，后续自动复用。
    """

    BROWSER_ARGS = BROWSER_ARGS
    LOGGER_NAME = "QwenClient"
    DISPLAY_NAME = "Qwen"
    DEFAULT_STORAGE_STATE_PATH = STORAGE_STATE_PATH

    def __init__(
        self,
        config: BrowserConfig | None = None,
        headless: bool | None = None,
        timeout: int | None = None,
        logger=None,
        user_data_dir: str | Path | None = None,
        storage_state_path: str | Path | None = None,
    ):
        """
        初始化客户端。
        :param headless: 是否无头模式
        :param timeout: 默认超时时间（毫秒）
        :param user_data_dir: Playwright 持久化用户数据目录，适合在有界面机器上完成登录
        :param storage_state_path: 可迁移登录态 JSON，适合复制到无界面开发机复用
        """
        super().__init__(
            adapter=QwenAdapter(),
            config=config,
            headless=headless,
            timeout=timeout,
            logger=logger,
            user_data_dir=user_data_dir,
            storage_state_path=storage_state_path,
        )

    def send_text(self, text: str, auto_remove_limit: bool = True, auto_close_guidance: bool = True) -> str:
        return super().send_text(
            text,
            auto_remove_limit=auto_remove_limit,
            auto_close_guidance=auto_close_guidance,
        )
