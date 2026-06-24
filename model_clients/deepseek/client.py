import re
from pathlib import Path

from agent_core.config import BrowserConfig

from ..browser_base import BrowserFrontendAdapter, BrowserModelClient

DEEPSEEK_URL = "https://chat.deepseek.com"
STORAGE_STATE_PATH = Path.cwd() / "config" / "storage_state_ds.json"
BROWSER_ARGS = ["--disable-blink-features=AutomationControlled"]
CONVERSATION_URL_RE = re.compile(r"/a/chat/s/([^/?#]+)")

COMPOSER_SELECTOR = 'textarea[name="search"]'

SEND_BUTTON_SELECTOR = (
    "[role='button'].ds-button--primary.ds-button--filled.ds-button--circle"
    ":not(.ds-button--disabled), "
    "button[type='submit']:not([disabled])"
)

UPLOAD_BUTTON_SELECTOR = (
    "[role='button'].ds-button--iconLabelPrimary.ds-button--icon"
    ".ds-button--capsule.ds-button--s, "
    "[role='button'].ds-button--iconLabelPrimary.ds-button--capsule, "
    "button[aria-label*='attach' i], button[aria-label*='upload' i], "
    "button[aria-label*='file' i], button[aria-label*='附件']"
)


class DeepSeekAdapter(BrowserFrontendAdapter):
    """DeepSeek-specific DOM selectors and browser interactions."""

    # ── 类属性 ──

    COMPOSER_SELECTOR = COMPOSER_SELECTOR
    SEND_BUTTON_SELECTOR = SEND_BUTTON_SELECTOR
    SENDABLE_FALLBACK_SELECTOR = COMPOSER_SELECTOR
    SEND_FAILURE_MESSAGE = "DeepSeek 发送失败：未检测到新用户消息或输入框清空"
    SEND_LOG_PREFIX = "DeepSeek "

    LATEST_REPLY_SCRIPT = "latest_reply.js"
    ASSISTANT_COUNT_SCRIPT = "assistant_count.js"
    GENERATION_IN_PROGRESS_SCRIPT = "generation_in_progress.js"
    USER_COUNT_SCRIPT = "user_count.js"
    USER_COUNT_ADVANCED_SCRIPT = "user_count_advanced.js"
    COMPOSER_EMPTY_SCRIPT = "composer_empty.js"
    LOGIN_STATE_SCRIPT = "login_state.js"
    UPLOADED_FILE_CARD_SCRIPT = "uploaded_file_card.js"

    def __init__(self):
        self.conversation_id: str | None = None

    # ── 抽象生命周期 ──

    def default_url(self) -> str:
        return DEEPSEEK_URL

    def is_logged_in(self) -> bool:
        try:
            current_url = self.page.url.lower()
            login_keywords = ["/sign_in", "/signin", "/login", "passport", "auth"]
            if any(keyword in current_url for keyword in login_keywords):
                self.logger.warning(f"检测到重定向至登录页: {current_url}")
                return False

            state = self.page.evaluate(self.load_js(self.LOGIN_STATE_SCRIPT)) or {}
            self.logger.debug(f"DeepSeek 登录状态检查: {state}")
            return bool(state.get("hasComposer"))
        except Exception as e:
            self.logger.warning(f"DeepSeek 登录状态检查失败: {type(e).__name__}: {e}")
            return False

    def wait_until_ready(self) -> None:
        self.page.wait_for_selector(COMPOSER_SELECTOR, timeout=self.config.timeout)
        self.page.wait_for_timeout(1000)
        self.logger.screenshot("wait_until_ready", full_page=True)

    # ── 可选生命周期钩子 ──

    def login_state_guidance(self, storage_state_path: str) -> str:
        return (
            "有图形界面时，在项目根目录运行 `python scripts/export_state.py --model deepseek`，"
            "按浏览器提示完成 DeepSeek 登录，脚本会导出 storage_state_ds.json。"
            "如果当前机器没有图形界面，请在本地电脑运行同一脚本，"
            f"再把生成的 storage_state_ds.json 上传到开发机的 {storage_state_path}。"
        )

    def after_message_sent(self) -> None:
        conversation_id = self.current_conversation_id()
        if not conversation_id or conversation_id == self.conversation_id:
            return
        self.conversation_id = conversation_id
        self.logger.debug(f"DeepSeek 当前会话 ID: {conversation_id}")

    def cleanup_session(self) -> None:
        if not self.conversation_id:
            self.logger.debug("DeepSeek 无已记录会话 ID，跳过会话清理")
            return
        try:
            self.delete_recorded_conversation()
        except Exception as e:
            self.logger.screenshot("cleanup_session", full_page=True)
            self.logger.warning(f"DeepSeek 会话清理失败: {type(e).__name__}: {e}")

    # ── 会话管理 (DeepSeek 特有) ──

    def current_conversation_id(self) -> str | None:
        match = CONVERSATION_URL_RE.search(self.page.url)
        return match.group(1) if match else None

    def delete_recorded_conversation(self) -> None:
        conversation_id = self.conversation_id
        if not conversation_id:
            return
        href = f"/a/chat/s/{conversation_id}"
        self.logger.debug(f"准备删除 DeepSeek 会话: {conversation_id}")
        self.logger.screenshot("delete_recorded_conversation", full_page=True)
        item = self.page.locator(f'a[href="{href}"]').first
        item.wait_for(state="visible", timeout=5000)
        item.hover(timeout=5000)
        menu_button = item.locator(
            "[role='button'].ds-button--iconLabelTertiary.ds-button--icon"
        ).first
        menu_button.wait_for(state="visible", timeout=5000)
        menu_button.click(timeout=5000)

        delete_option = self.page.locator(
            ".ds-dropdown-menu[role='menu'] .ds-dropdown-menu-option--error"
        ).first
        delete_option.wait_for(state="visible", timeout=5000)
        delete_option.click(timeout=5000)
        self._confirm_delete_conversation_if_needed()
        self.page.wait_for_timeout(1000)
        self.page.wait_for_function(
            """
            (href) => !document.querySelector(`a[href="${href}"]`)
            """,
            arg=href,
            timeout=10000,
        )
        self.logger.debug(f"DeepSeek 会话已删除: {conversation_id}")
        self.conversation_id = None

    # ── 文本发送 ──

    def before_text_send(self, text: str, **kwargs):
        composer = self._composer_locator()
        composer.wait_for(state="visible", timeout=self.config.timeout)
        previous_user_message_count = self._user_message_count()
        previous_assistant_message_count = self.assistant_message_count()

        try:
            composer.fill(text)
        except Exception:
            composer.click()
            self.page.keyboard.press("Control+A")
            self.page.keyboard.insert_text(text)
        self.page.wait_for_timeout(300)
        return {
            "previous_user_message_count": previous_user_message_count,
            "previous_assistant_message_count": previous_assistant_message_count,
            "had_text": True,
        }

    # ── 文件上传 ──

    def before_file_send(self, file_path: Path, prompt: str = None):
        self.logger.debug(f"准备上传 DeepSeek 文件: {file_path}")
        return {
            "previous_user_message_count": self._user_message_count(),
            "previous_assistant_message_count": self.assistant_message_count(),
            "prefer_button": True,
            "had_text": bool(prompt),
        }

    def upload_file(self, file_path: Path) -> None:
        errors = []
        for upload in (
            self._upload_by_input,
            self._upload_by_file_chooser,
            self._upload_by_input,
        ):
            try:
                upload(file_path)
                self._wait_for_uploaded_file_card(file_path)
                return
            except Exception as e:
                errors.append(f"{upload.__name__}: {type(e).__name__}: {e}")
                self.logger.debug(errors[-1])
        raise RuntimeError("DeepSeek 文件上传失败：未找到可用的文件输入或上传按钮")

    # ── 内部工具 ──

    def _upload_by_input(self, file_path: Path) -> None:
        file_input = self.page.locator("input[type=file]").first
        file_input.wait_for(state="attached", timeout=5000)
        file_input.set_input_files(str(file_path))

    def _upload_by_file_chooser(self, file_path: Path) -> None:
        attach_button = self.page.locator(UPLOAD_BUTTON_SELECTOR).first
        attach_button.wait_for(state="visible", timeout=5000)
        with self.page.expect_file_chooser(timeout=5000) as chooser_info:
            attach_button.click(timeout=5000)
        chooser_info.value.set_files(str(file_path))

    def _wait_for_uploaded_file_card(self, file_path: Path) -> None:
        try:
            self.page.wait_for_function(
                self.load_js(self.UPLOADED_FILE_CARD_SCRIPT),
                arg=file_path.name,
                timeout=5000,
            )
        except Exception as e:
            self.logger.debug(
                "未观察到 DeepSeek 文件卡片，继续尝试发送: " f"{type(e).__name__}: {e}"
            )

    def _confirm_delete_conversation_if_needed(self) -> None:
        selectors = (
            ".ds-modal-content[role='dialog'] [role='button'].ds-button--error",
            ".ds-modal-focus-lock [role='dialog'] [role='button'].ds-button--error",
            "[role='dialog'] [role='button'].ds-button--error",
        )
        for selector in selectors:
            try:
                button = self.page.locator(selector).first
                button.wait_for(state="visible", timeout=3000)
                button.click(timeout=5000)
                return
            except Exception:
                continue


class DeepSeekClient(BrowserModelClient):
    """DeepSeek 网页版自动化客户端（基于持久化会话）。"""

    BROWSER_ARGS = BROWSER_ARGS
    LOGGER_NAME = "DeepSeekClient"
    DISPLAY_NAME = "DeepSeek"
    DEFAULT_STORAGE_STATE_PATH = STORAGE_STATE_PATH

    def __init__(
        self,
        config: BrowserConfig | None = None,
        headless: bool | None = None,
        timeout: int | None = None,
        logger=None,
        storage_state_path: str | Path | None = None,
        cleanup_session: bool = False,
    ):
        super().__init__(
            adapter=DeepSeekAdapter(),
            config=config,
            headless=headless,
            timeout=timeout,
            logger=logger,
            storage_state_path=storage_state_path,
            cleanup_session=cleanup_session,
        )
