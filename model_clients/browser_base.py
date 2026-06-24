import time
from abc import ABC, abstractmethod
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from agent_core.config import BrowserConfig
from utils import get_logger


class BrowserClientConfig(BrowserConfig):
    browser_args: list[str] | None = None
    init_script: str | None = None


class BrowserFrontendAdapter(ABC):
    """Site-specific browser behavior used by BrowserModelClient workflows."""

    # ── 类属性 (CSS 选择器 & JS 脚本引用) ──

    COMPOSER_SELECTOR: str | None = None
    SEND_BUTTON_SELECTOR: str | None = None
    SENDABLE_FALLBACK_SELECTOR: str | None = None
    SEND_FAILURE_MESSAGE = "发送失败：未检测到新用户消息或输入框清空"
    SEND_LOG_PREFIX = ""

    LATEST_REPLY_SCRIPT: str | None = None
    ASSISTANT_COUNT_SCRIPT: str | None = None
    GENERATION_IN_PROGRESS_SCRIPT: str | None = None
    REPLY_COMPLETE_SCRIPT: str | None = None
    USER_COUNT_SCRIPT: str | None = None
    USER_COUNT_ADVANCED_SCRIPT: str | None = None
    COMPOSER_EMPTY_SCRIPT: str | None = None

    # ── JS 脚本加载 ──

    @classmethod
    def script_package(cls) -> str:
        return cls.__module__.rsplit(".", 1)[0]

    @classmethod
    @lru_cache(maxsize=None)
    def load_js(cls, filename: str) -> str:
        return (
            resources.files(cls.script_package())
            .joinpath("scripts", filename)
            .read_text(encoding="utf-8")
        )

    def _script_text(self, filename: str | None) -> str | None:
        return self.load_js(filename) if filename else None

    # ── 绑定 & 属性 ──

    def bind(self, session, config: BrowserClientConfig, logger) -> None:
        self.session = session
        self.config = config
        self.logger = logger

    @property
    def page(self):
        return self.session.page

    # ── 抽象生命周期 (子类必须实现) ──

    @abstractmethod
    def default_url(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def is_logged_in(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def wait_until_ready(self) -> None:
        raise NotImplementedError

    # ── 可选生命周期钩子 ──

    def after_page_loaded(self) -> None:
        pass

    def login_state_guidance(self, storage_state_path: str) -> str:
        return (
            f"请在有图形界面的机器登录目标站点，并导出 Playwright storage_state 到 {storage_state_path}。"
            "如果当前机器没有图形界面，请在本地电脑导出后上传该文件。"
        )

    def after_message_sent(self) -> None:
        pass

    def cleanup_session(self) -> None:
        pass

    # ── 文本发送 ──

    def before_text_send(self, text: str, **kwargs):
        return None

    def wait_until_sendable(self, timeout: int = None) -> None:
        if not self.SEND_BUTTON_SELECTOR:
            return
        wait_timeout = timeout or self.config.timeout
        try:
            self.page.wait_for_selector(
                self.SEND_BUTTON_SELECTOR,
                state="visible",
                timeout=wait_timeout,
            )
        except Exception:
            if not self.SENDABLE_FALLBACK_SELECTOR:
                raise
            self.page.wait_for_selector(
                self.SENDABLE_FALLBACK_SELECTOR,
                state="visible",
                timeout=wait_timeout,
            )

    def send_current_message(
        self,
        previous_user_message_count: int = None,
        prefer_button: bool = False,
        had_text: bool = True,
    ) -> None:
        send_attempts = ("button", "enter") if prefer_button else ("enter", "button")
        for method in send_attempts:
            try:
                if method == "enter":
                    self._composer_locator().press("Enter")
                else:
                    self.wait_until_sendable(timeout=5000)
                    self._send_button_locator().click(timeout=5000)
                if self._is_sent(previous_user_message_count, had_text=had_text):
                    return
            except Exception as e:
                self.logger.debug(
                    f"{self.SEND_LOG_PREFIX}{method} 发送异常: {type(e).__name__}: {e}"
                )
        raise RuntimeError(self.SEND_FAILURE_MESSAGE)

    # ── 文件上传 ──

    def before_file_send(self, file_path: Path, prompt: str = None):
        return None

    def upload_file(self, file_path: Path) -> None:
        raise NotImplementedError

    def fill_prompt_after_upload(self, prompt: str):
        composer = self._composer_locator()
        composer.wait_for(state="visible", timeout=self.config.timeout)
        try:
            composer.fill(prompt)
        except Exception:
            composer.click()
            self.page.keyboard.insert_text(prompt)
        return {"had_text": True}

    # ── 回复检测 ──

    def latest_reply_text(self) -> str:
        return self._evaluate_string_script(self._script_text(self.LATEST_REPLY_SCRIPT))

    def is_generation_in_progress(self) -> bool:
        return self._evaluate_bool_script(
            self._script_text(self.GENERATION_IN_PROGRESS_SCRIPT)
        )

    def is_reply_complete(self) -> bool:
        if self.REPLY_COMPLETE_SCRIPT:
            return self._evaluate_bool_script(
                self._script_text(self.REPLY_COMPLETE_SCRIPT)
            )
        if self.GENERATION_IN_PROGRESS_SCRIPT:
            return not self.is_generation_in_progress()
        return False

    def assistant_message_count(self) -> int | None:
        return self._evaluate_int_script(self._script_text(self.ASSISTANT_COUNT_SCRIPT))

    def try_handle_reply_preference_ui(self) -> str:
        return ""

    # ── 内部工具 (playwright 底层操作封装) ──

    def _composer_locator(self):
        if not self.COMPOSER_SELECTOR:
            raise NotImplementedError("adapter must define COMPOSER_SELECTOR")
        return self.page.locator(self.COMPOSER_SELECTOR).first

    def _send_button_locator(self):
        if not self.SEND_BUTTON_SELECTOR:
            raise NotImplementedError("adapter must define SEND_BUTTON_SELECTOR")
        return self.page.locator(self.SEND_BUTTON_SELECTOR).first

    def _user_message_count(self) -> int:
        count = self._evaluate_int_script(self._script_text(self.USER_COUNT_SCRIPT))
        return count if count is not None else 0

    def _is_sent(
        self, previous_user_message_count: int = None, had_text: bool = True
    ) -> bool:
        if previous_user_message_count is not None and self.USER_COUNT_ADVANCED_SCRIPT:
            try:
                self.page.wait_for_function(
                    self._script_text(self.USER_COUNT_ADVANCED_SCRIPT),
                    arg=previous_user_message_count,
                    timeout=8000,
                )
                return True
            except Exception:
                pass

        if not had_text:
            return False

        if not self.COMPOSER_EMPTY_SCRIPT:
            return False
        try:
            self.page.wait_for_function(
                self._script_text(self.COMPOSER_EMPTY_SCRIPT), timeout=5000
            )
            return True
        except Exception:
            return False

    def _evaluate_string_script(self, script: str | None) -> str:
        if not script:
            return ""
        try:
            return (self.page.evaluate(script) or "").strip()
        except Exception:
            return ""

    def _evaluate_int_script(self, script: str | None) -> int | None:
        if not script:
            return None
        try:
            return int(self.page.evaluate(script))
        except Exception:
            return None

    def _evaluate_bool_script(self, script: str | None) -> bool:
        if not script:
            return False
        try:
            return bool(self.page.evaluate(script))
        except Exception:
            return False


class BrowserSession:
    """Owns Playwright browser/context/page lifecycle and login state persistence."""

    def __init__(self, config: BrowserClientConfig, logger):
        self.config = config
        self.logger = logger
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.notices: list[dict[str, str]] = []

    def start(self, url: str, adapter: BrowserFrontendAdapter) -> None:
        self.playwright = sync_playwright().start()
        storage_state_path = self.resolve_storage_state_path()
        browser_args = self.config.browser_args or []

        self.browser = self.playwright.chromium.launch(
            headless=self.config.headless,
            args=browser_args,
        )
        if storage_state_path:
            self.context = self.browser.new_context(
                storage_state=str(storage_state_path)
            )
            self.logger.info(f"已加载 storage_state 登录状态: {storage_state_path}")
        else:
            self.context = self.browser.new_context()
            self.logger.warning(
                self._missing_login_state_message(adapter, reason="未找到登录态文件")
            )

        self.page = self.context.new_page()
        if self.config.init_script:
            self.page.add_init_script(self.config.init_script)
        self.logger.attach_page(self.page)
        self.logger.info("Starting browser...")
        self.page.goto(url, wait_until="domcontentloaded", timeout=self.config.timeout)
        self._add_notice(
            "info",
            "当前处于测试版，不太稳定，遇到错误回答时请首先尝试重新提问，或退出并重启session",
        )

        logged_in = adapter.is_logged_in()
        if not logged_in:
            self._add_notice(
                "warning",
                self._missing_login_state_message(adapter, reason="未检测到有效登录态"),
            )
            if not self.config.headless:
                self.wait_for_login(adapter)
                self.save_storage_state()
                self.logger.info("检测到登录成功并保存登录状态")
                logged_in = True

        if logged_in:
            adapter.wait_until_ready()
            adapter.after_page_loaded()
            self.logger.debug("Page loaded")
        else:
            self.logger.warning(
                "未登录 fallback 模式已启动，后续模型交互可能无法正常工作。"
            )

    def close(self) -> None:
        if self.context:
            self.context.close()
        if self.browser and self.browser is not self.context:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        self.logger.info("Browser closed")

    def resolve_storage_state_path(self) -> Path | None:
        storage_state_path = self.config.storage_state_path
        if storage_state_path and storage_state_path.exists():
            return storage_state_path
        return None

    def consume_notices(self) -> list[dict[str, str]]:
        notices = self.notices
        self.notices = []
        return notices

    def _add_notice(self, level: str, message: str) -> None:
        notice = {"level": level, "message": message}
        if notice not in self.notices:
            self.notices.append(notice)
        if level == "warning":
            self.logger.warning(message)
        else:
            self.logger.info(message)

    def _storage_state_path_text(self) -> str:
        storage_state_path = self.config.storage_state_path
        return (
            str(storage_state_path)
            if storage_state_path
            else "对应模型客户端默认登录态路径"
        )

    def _missing_login_state_message(
        self, adapter: BrowserFrontendAdapter, reason: str
    ) -> str:
        storage_state_path = self._storage_state_path_text()
        return (
            f"{reason}，将以未登录模式启动。\n"
            "注意：该模式可能无法正常使用模型！\n"
            f"请获取登录态并保存为 {storage_state_path}。\n"
            f"{adapter.login_state_guidance(storage_state_path)}"
        )

    def save_storage_state(self) -> None:
        storage_state_path = self.config.storage_state_path
        if not storage_state_path:
            return
        storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.context.storage_state(path=str(storage_state_path), indexed_db=True)
        except TypeError:
            self.context.storage_state(path=str(storage_state_path))
        self.logger.info(f"已保存 storage_state: {storage_state_path}")

    def wait_for_login(self, adapter: BrowserFrontendAdapter) -> None:
        deadline = time.time() + self.config.timeout / 1000
        while time.time() < deadline:
            if adapter.is_logged_in():
                return
            self.page.wait_for_timeout(1000)
        raise TimeoutError("等待手动登录超时")


class BrowserConversationWorkflow:
    """Shared model-message workflows built from frontend adapter operations."""

    def __init__(
        self,
        session: BrowserSession,
        adapter: BrowserFrontendAdapter,
        config: BrowserClientConfig,
        logger,
    ):
        self.session = session
        self.adapter = adapter
        self.config = config
        self.logger = logger

    @property
    def page(self):
        return self.session.page

    def wait_for_reply(
        self, previous_assistant_message_count: int | None = None
    ) -> str:
        deadline = time.time() + self.config.timeout / 1000
        last_reply = ""
        completed_reply = ""
        completed_at = None
        logged_waiting_for_baseline = False
        stable_completion_seconds = 1.5

        while time.time() < deadline:
            baseline_advanced = True
            if previous_assistant_message_count is not None:
                current_assistant_message_count = self.adapter.assistant_message_count()
                if (
                    current_assistant_message_count is not None
                    and current_assistant_message_count
                    < previous_assistant_message_count
                ):
                    self.logger.debug(
                        "assistant 回复节点数量回退，重置等待 baseline: "
                        f"当前 {current_assistant_message_count}, baseline {previous_assistant_message_count}"
                    )
                    previous_assistant_message_count = current_assistant_message_count
                    logged_waiting_for_baseline = False
                baseline_advanced = (
                    current_assistant_message_count is not None
                    and current_assistant_message_count
                    > previous_assistant_message_count
                )
                if not baseline_advanced and not logged_waiting_for_baseline:
                    self.logger.debug(
                        "等待新 assistant 回复节点出现: "
                        f"当前 {current_assistant_message_count}, baseline {previous_assistant_message_count}"
                    )
                    logged_waiting_for_baseline = True

            if baseline_advanced:
                ab_reply = self.adapter.try_handle_reply_preference_ui()
                if ab_reply:
                    return ab_reply

                reply = self.adapter.latest_reply_text()
                if reply and reply != last_reply:
                    last_reply = reply
                    completed_reply = ""
                    completed_at = None
                    self.logger.debug(f"检测到回复更新，当前长度 {len(reply)}")

                if (
                    reply
                    and self.adapter.is_reply_complete()
                    and not self.adapter.is_generation_in_progress()
                ):
                    now = time.time()
                    if completed_reply != reply:
                        completed_reply = reply
                        completed_at = now
                    elif (
                        completed_at is not None
                        and now - completed_at >= stable_completion_seconds
                    ):
                        self.logger.debug(f"回复完成，长度 {len(reply)}")
                        return reply
                else:
                    completed_reply = ""
                    completed_at = None

            self.page.wait_for_timeout(500)

        self.logger.screenshot("wait_for_reply_timeout", full_page=True)
        if last_reply:
            self.logger.warning(
                f"等待回复完成超时，返回已捕获回复，长度 {len(last_reply)}"
            )
            return last_reply
        raise TimeoutError("等待 AI 回复超时，请注意是否达到今日额度上限")

    def send_text(self, text: str, **options) -> str:
        send_kwargs = self._as_kwargs(self.adapter.before_text_send(text, **options))
        previous_assistant_message_count = send_kwargs.pop(
            "previous_assistant_message_count", None
        )
        self.adapter.send_current_message(**send_kwargs)
        self.adapter.after_message_sent()
        self.logger.debug("消息已发送，等待 AI 回复...")
        return self.wait_for_reply(
            previous_assistant_message_count=previous_assistant_message_count
        )

    def send_file(self, file_path: str, prompt: str = None) -> str:
        resolved_path = Path(file_path).expanduser().resolve()
        if not resolved_path.exists():
            raise FileNotFoundError(f"文件不存在: {resolved_path}")
        if not resolved_path.is_file():
            raise ValueError(f"不是普通文件: {resolved_path}")

        try:
            send_kwargs = self._as_kwargs(
                self.adapter.before_file_send(resolved_path, prompt=prompt)
            )
            previous_assistant_message_count = send_kwargs.pop(
                "previous_assistant_message_count", None
            )
            self.logger.screenshot("send_file_before_upload", full_page=False)
            self.adapter.upload_file(resolved_path)
            self.logger.info(f"已上传文件: {resolved_path}")

            self.adapter.wait_until_sendable()
            if prompt:
                prompt_kwargs = self.adapter.fill_prompt_after_upload(prompt)
                if prompt_kwargs:
                    send_kwargs = {**send_kwargs, **self._as_kwargs(prompt_kwargs)}
                self.adapter.wait_until_sendable()
                self.logger.screenshot("send_file_after_prompt", full_page=False)

            self.logger.screenshot("send_file_before_send", full_page=False)
            self.adapter.send_current_message(**send_kwargs)
            self.adapter.after_message_sent()
            self.logger.screenshot("send_file_after_send", full_page=False)
            self.logger.debug("已发送文件消息，等待 AI 回复...")
            return self.wait_for_reply(
                previous_assistant_message_count=previous_assistant_message_count
            )
        except Exception:
            self.logger.screenshot("send_file_error", full_page=True)
            raise

    def _as_kwargs(self, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}


class BrowserModelClient:
    """Thin public client facade assembled from session, workflow, adapter, and config."""

    BROWSER_ARGS: list[str] = []
    INIT_SCRIPT = """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    """
    LOGGER_NAME = "BrowserModelClient"
    LOG_DIR = "./debug"
    SCREENSHOT_DIR = "./debug/screenshots"
    DEFAULT_STORAGE_STATE_PATH: Path | None = None

    def __init__(
        self,
        adapter: BrowserFrontendAdapter,
        config: BrowserConfig | None = None,
        headless: bool | None = None,
        timeout: int | None = None,
        logger=None,
        storage_state_path: str | Path | None = None,
        cleanup_session: bool = False,
    ):
        base_config = config or BrowserConfig()
        resolved_storage_state_path = storage_state_path
        if resolved_storage_state_path is None:
            resolved_storage_state_path = (
                base_config.storage_state_path or self.DEFAULT_STORAGE_STATE_PATH
            )
        config = BrowserClientConfig(
            headless=base_config.headless if headless is None else headless,
            timeout=base_config.timeout if timeout is None else timeout,
            storage_state_path=self._resolve_optional_path(resolved_storage_state_path),
            browser_args=self.BROWSER_ARGS,
            init_script=self.INIT_SCRIPT,
        )
        self.config = config
        self.logger = logger or get_logger(
            self.LOGGER_NAME,
            log_dir=self.LOG_DIR,
            screenshot_dir=self.SCREENSHOT_DIR,
        )
        self.cleanup_session_on_close = cleanup_session
        self.adapter = adapter
        self.session = BrowserSession(config, self.logger)
        self.adapter.bind(self.session, config, self.logger)
        self.workflow = BrowserConversationWorkflow(
            self.session, self.adapter, config, self.logger
        )

    def _resolve_optional_path(self, path: str | Path | None) -> Path | None:
        return Path(path).expanduser().resolve() if path else None

    @property
    def timeout(self) -> int:
        return self.config.timeout

    def start(self, url: str = None):
        self.session.start(url or self.adapter.default_url(), self.adapter)

    def consume_notices(self) -> list[dict[str, str]]:
        return self.session.consume_notices()

    def close(self):
        if self.cleanup_session_on_close:
            self.adapter.cleanup_session()
        self.session.close()

    def wait_for_reply(self) -> str:
        return self.workflow.wait_for_reply()

    def send_text(self, text: str, **options) -> str:
        return self.workflow.send_text(text, **options)

    def send_file(self, file_path: str, prompt: str = None) -> str:
        return self.workflow.send_file(file_path, prompt=prompt)
