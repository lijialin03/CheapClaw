from pathlib import Path

from cheapclaw.agent_core.config import BrowserConfig

from ..browser_base import BrowserFrontendAdapter, BrowserModelClient
from ..selector_config import SelectorConfig, load_selector_config

STORAGE_STATE_PATH = Path.cwd() / "config" / "storage_state_qwen.json"

BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
]


class QwenAdapter(BrowserFrontendAdapter):
    """Qwen-specific DOM selectors and browser interactions."""

    # ── 类属性 ──

    SEND_FAILURE_MESSAGE = "发送失败：未检测到新用户消息或发送完成状态"

    LATEST_REPLY_SCRIPT = "latest_reply.js"
    ASSISTANT_COUNT_SCRIPT = "assistant_count.js"
    GENERATION_IN_PROGRESS_SCRIPT = "generation_in_progress.js"
    REPLY_COMPLETE_SCRIPT = "reply_complete.js"
    USER_COUNT_SCRIPT = "user_count.js"
    USER_COUNT_ADVANCED_SCRIPT = "user_count_advanced.js"
    COMPOSER_EMPTY_SCRIPT = "composer_empty.js"
    LOGIN_STATE_SCRIPT = "login_state.js"
    OVERLAY_AUTO_DISMISS_SCRIPT = "overlay_auto_dismiss.js"
    REPLY_PREFERENCE_SCRIPT = "reply_preference.js"

    def __init__(self, selector_config: SelectorConfig | None = None):
        self.selector_config = selector_config or load_selector_config("qwen")

    # ── 抽象生命周期 ──

    def default_url(self) -> str:
        return "https://chat.qwen.ai/"

    def is_logged_in(self) -> bool:
        """检查当前页面是否已登录。"""
        try:
            current_url = self.page.url.lower()
            login_keywords = self.selector_config.login_url_keywords
            if any(keyword in current_url for keyword in login_keywords):
                self.logger.warning(f"检测到重定向至登录页: {current_url}")
                return False

            state = self.page.evaluate(
                self.load_js(self.LOGIN_STATE_SCRIPT), self._selector_args()
            )
            self.logger.debug(f"登录状态检查: {state}")
            return state.get("authStatus") == 200 and not state.get("authButtons")
        except Exception as e:
            self.logger.warning(f"登录状态检查失败: {type(e).__name__}: {e}")
            return False

    def wait_until_ready(self) -> None:
        self.page.wait_for_selector(
            self.selector_config.composer, timeout=self.config.timeout
        )
        self.page.wait_for_timeout(1000)

    # ── 可选生命周期钩子 ──

    def login_state_guidance(self, storage_state_path: str) -> str:
        return (
            "有图形界面时，在项目根目录运行 `python -m cheapclaw.scripts.export_state --model qwen`，"
            "按浏览器提示完成 Qwen 登录，脚本会导出 storage_state_qwen.json。"
            "如果当前机器没有图形界面，请在本地电脑运行同一脚本，"
            f"再把生成的 storage_state_qwen.json 上传到开发机的 {storage_state_path}。"
            "导出脚本支持密码、GitHub、二维码等网页登录方式。"
        )

    def after_page_loaded(self) -> None:
        self._inject_overlay_auto_dismiss()

    def detect_generation_error(self) -> str | None:
        """检测 Qwen 页面是否显示模型错误。"""
        try:
            error_texts = [
                "请求过于频繁",
                "服务不可用",
                "Service Unavailable",
                "Something went wrong",
                "额度上限",
            ]
            body_text = (self.page.inner_text("body") or "").lower()
            for text in error_texts:
                if text.lower() in body_text:
                    return f"检测到 Qwen 生成错误: {text}"
        except Exception:
            pass
        return None

    # ── 文本发送 ──

    def before_text_send(
        self,
        text: str,
        auto_remove_limit: bool = True,
        auto_close_guidance: bool = True,
    ):
        textarea = self.page.locator(self.selector_config.composer)
        textarea.wait_for(state="visible", timeout=self.config.timeout)

        if auto_close_guidance:
            self._try_close_guidance()
            textarea = self.page.locator(self.selector_config.composer)
            textarea.wait_for(state="visible", timeout=self.config.timeout)

        if auto_remove_limit:
            self.page.evaluate(
                f"""() => {{
                    const textarea = document.querySelector('{self.selector_config.composer}');
                    if (textarea) textarea.removeAttribute('maxlength');
                }}"""
            )

        textarea.click()
        textarea.fill(text)
        self.page.wait_for_timeout(300)
        return {
            "previous_user_message_count": self._user_message_count(),
            "previous_assistant_message_count": self.assistant_message_count(),
        }

    # ── 文件上传 ──

    def before_file_send(self, file_path: Path, prompt: str = None):
        self.logger.debug(f"准备上传文件: {file_path}")
        self._try_close_guidance()
        return {
            "previous_user_message_count": self._user_message_count(),
            "previous_assistant_message_count": self.assistant_message_count(),
            "prefer_button": True,
            "had_text": bool(prompt),
        }

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
            self.logger.debug(
                f"上传菜单方式失败，回退隐藏 input: {type(e).__name__}: {e}"
            )
            self.logger.screenshot("send_file_upload_menu_failed", full_page=False)
            self._upload_file_by_input(file_path)

        self._wait_for_uploaded_file_card(file_path)
        self.logger.info("文件已显示在输入框附件卡片中")
        self.logger.screenshot("send_file_after_upload_card", full_page=False)

    # ── 回复检测 ──

    def try_handle_reply_preference_ui(self) -> str:
        """处理 Qwen 偶发的"您更喜欢哪个回复"双回复评测 UI，默认选择第一个回复。"""
        try:
            if self.page.locator("text=您更喜欢哪个回复").count() == 0:
                return ""

            self.logger.warning("检测到双回复评测 UI，默认选择第一个回复")
            self.logger.screenshot("reply_preference_ui", full_page=True)
            result = self.page.evaluate(self.load_js(self.REPLY_PREFERENCE_SCRIPT))
            reply = (result or {}).get("reply", "").strip()
            if not (result or {}).get("clicked"):
                self.logger.warning("未能点击第一个偏好回复按钮")
            self.page.wait_for_timeout(1000)
            return reply
        except Exception as e:
            self.logger.debug(f"处理双回复评测 UI 失败: {type(e).__name__}: {e}")
            return ""

    # ── 内部工具 (Qwen 特有) ──

    def _open_upload_menu(self) -> None:
        """点击输入框左侧加号，打开包含"上传附件"的菜单。"""
        trigger = self.selector_config.upload_menu_trigger
        plus_button = self.page.locator(trigger).first
        plus_button.wait_for(state="visible", timeout=5000)
        plus_button.click(timeout=5000)

    def _upload_attachment_item(self):
        """返回上传附件菜单项；该菜单可能挂载在 body 弹层中。"""
        item_selector = self.selector_config.upload_menu_item or "text=上传附件"
        return self.page.locator(item_selector).first

    def _upload_file_by_input(self, file_path: Path) -> None:
        file_input = self.page.locator(
            self.selector_config.upload_file_input or "#filesUpload"
        )
        file_input.wait_for(state="attached", timeout=self.config.timeout)
        file_input.set_input_files(str(file_path))

    def _wait_for_uploaded_file_card(self, file_path: Path) -> None:
        """等待上传后的文件卡片出现在输入框附件区域。"""
        stem = file_path.stem
        suffix = file_path.suffix
        cfg = self.selector_config
        card_list = (
            cfg.upload_file_card_list or ".message-input-column-file .file-card-list"
        )
        card_item = cfg.upload_file_card_item or ".fileitem-btn"
        card_name = cfg.upload_file_card_name or ".fileitem-file-name-text"
        card_ext = cfg.upload_file_card_ext or ".fileitem-file-name-ext"

        self.page.wait_for_selector(
            card_list,
            state="visible",
            timeout=self.config.timeout,
        )
        self.page.wait_for_function(
            f"""({{stem, suffix}}) => {{
                const cards = Array.from(document.querySelectorAll('{card_item}'));
                return cards.some((card) => {{
                    const name = card.querySelector('{card_name}')?.textContent?.trim() || '';
                    const ext = card.querySelector('{card_ext}')?.textContent?.trim() || '';
                    const fullName = `${{name}}${{ext}}`;
                    return (name === stem && ext === suffix) || fullName === `${{stem}}${{suffix}}`;
                }});
            }}""",
            arg={"stem": stem, "suffix": suffix},
            timeout=self.config.timeout,
        )

    def _try_close_guidance(self) -> bool:
        """如果当前有首页引导/示例区域，尝试关闭它。"""
        if not self.selector_config.guidance_close_button:
            return False
        try:
            selectors = [
                s.strip() for s in self.selector_config.guidance_close_button.split(",")
            ]
            selector = ", ".join(selectors)
            close_button = self.page.locator(selector).first
            if not close_button.is_visible(timeout=1000):
                return False
            close_button.click(timeout=5000)
            self.page.wait_for_timeout(1000)
            return True
        except Exception:
            return False

    def _inject_overlay_auto_dismiss(self):
        """自动关闭可能遮挡输入区的弹窗。"""
        self.page.evaluate(self.load_js(self.OVERLAY_AUTO_DISMISS_SCRIPT))
        self.logger.debug("弹窗自动关闭 hook 已注入")


class QwenClient(BrowserModelClient):
    """
    Qwen网页版自动化客户端（基于持久化会话）。
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
        storage_state_path: str | Path | None = None,
        cleanup_session: bool = False,
    ):
        """
        初始化客户端。
        :param headless: 是否无头模式
        :param timeout: 默认超时时间（毫秒）
        :param storage_state_path: 可迁移登录态 JSON，适合复制到无界面开发机复用
        """
        adapter = QwenAdapter()
        super().__init__(
            adapter=adapter,
            config=config,
            headless=headless,
            timeout=timeout,
            logger=logger,
            storage_state_path=storage_state_path,
            cleanup_session=cleanup_session,
        )

    def send_text(
        self,
        text: str,
        auto_remove_limit: bool = True,
        auto_close_guidance: bool = True,
    ) -> str:
        return super().send_text(
            text,
            auto_remove_limit=auto_remove_limit,
            auto_close_guidance=auto_close_guidance,
        )
