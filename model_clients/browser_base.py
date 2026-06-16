import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright
from utils import get_logger


@dataclass
class BrowserClientConfig:
    headless: bool = False
    timeout: int = 120000
    user_data_dir: Path | None = None
    storage_state_path: Path | None = None
    browser_args: list[str] | None = None
    init_script: str | None = None


class BrowserFrontendAdapter(ABC):
    """Site-specific browser behavior used by BrowserModelClient workflows."""

    def bind(self, session, config: BrowserClientConfig, logger) -> None:
        self.session = session
        self.config = config
        self.logger = logger

    @property
    def page(self):
        return self.session.page

    @abstractmethod
    def default_url(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def is_logged_in(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def wait_until_ready(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def latest_reply_text(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def is_reply_complete(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def is_generation_in_progress(self) -> bool:
        raise NotImplementedError

    def assistant_message_count(self) -> int | None:
        return None

    @abstractmethod
    def send_current_message(self, **kwargs) -> None:
        raise NotImplementedError

    def after_page_loaded(self) -> None:
        pass

    def before_text_send(self, text: str, **kwargs):
        return None

    def before_file_send(self, file_path: Path, prompt: str = None):
        return None

    def try_handle_reply_preference_ui(self) -> str:
        return ""

    def wait_until_sendable(self, timeout: int = None) -> None:
        pass

    def upload_file(self, file_path: Path) -> None:
        raise NotImplementedError

    def fill_prompt_after_upload(self, prompt: str):
        return None


class BrowserSession:
    """Owns Playwright browser/context/page lifecycle and login state persistence."""

    def __init__(self, config: BrowserClientConfig, logger):
        self.config = config
        self.logger = logger
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def start(self, url: str, adapter: BrowserFrontendAdapter) -> None:
        self.playwright = sync_playwright().start()
        storage_state_path = self.resolve_storage_state_path()
        browser_args = self.config.browser_args or []

        if self.config.user_data_dir:
            self.config.user_data_dir.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"使用持久化用户数据目录: {self.config.user_data_dir}")
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.config.user_data_dir),
                headless=self.config.headless,
                args=browser_args,
            )
            self.browser = self.context
            self.logger.info("已加载持久化用户数据目录。")
        elif storage_state_path:
            self.browser = self.playwright.chromium.launch(
                headless=self.config.headless,
                args=browser_args,
            )
            self.context = self.browser.new_context(storage_state=str(storage_state_path))
            self.logger.info(f"已加载 storage_state 登录状态: {storage_state_path}")
        else:
            self.browser = self.playwright.chromium.launch(
                headless=self.config.headless,
                args=browser_args,
            )
            self.context = self.browser.new_context()
            self.logger.info("未找到登录状态，以未登录模式启动。")

        self.page = self.context.new_page()
        if self.config.init_script:
            self.page.add_init_script(self.config.init_script)
        self.logger.attach_page(self.page)
        self.logger.info("Starting browser...")
        self.page.goto(url, wait_until="domcontentloaded", timeout=self.config.timeout)

        if not adapter.is_logged_in():
            if self.config.headless:
                raise RuntimeError(
                    f"当前是 headless 模式且未登录。请先在有界面机器导出 {self.config.storage_state_path} 后复制到开发机。"
                )
            self.logger.warning("登录状态已过期或未登录，请在浏览器窗口中手动登录...")
            self.wait_for_login(adapter)
            self.save_storage_state()
            self.logger.info("检测到登录成功并保存登录状态")

        adapter.wait_until_ready()
        adapter.after_page_loaded()
        self.logger.debug("Page loaded")

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

    def __init__(self, session: BrowserSession, adapter: BrowserFrontendAdapter, config: BrowserClientConfig, logger):
        self.session = session
        self.adapter = adapter
        self.config = config
        self.logger = logger

    @property
    def page(self):
        return self.session.page

    def wait_for_reply(self, previous_assistant_message_count: int | None = None) -> str:
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
                baseline_advanced = (
                    current_assistant_message_count is not None
                    and current_assistant_message_count > previous_assistant_message_count
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

                if reply and self.adapter.is_reply_complete() and not self.adapter.is_generation_in_progress():
                    now = time.time()
                    if completed_reply != reply:
                        completed_reply = reply
                        completed_at = now
                    elif completed_at is not None and now - completed_at >= stable_completion_seconds:
                        self.logger.debug(f"回复完成，长度 {len(reply)}")
                        return reply
                else:
                    completed_reply = ""
                    completed_at = None

            self.page.wait_for_timeout(500)

        self.logger.screenshot("wait_for_reply_timeout", full_page=True)
        if last_reply:
            self.logger.warning(f"等待回复完成超时，返回已捕获回复，长度 {len(last_reply)}")
            return last_reply
        raise TimeoutError("等待 AI 回复超时")

    def send_text(self, text: str, **options) -> str:
        send_kwargs = self._as_kwargs(self.adapter.before_text_send(text, **options))
        previous_assistant_message_count = send_kwargs.pop("previous_assistant_message_count", None)
        self.adapter.send_current_message(**send_kwargs)
        self.logger.debug("消息已发送，等待 AI 回复...")
        return self.wait_for_reply(previous_assistant_message_count=previous_assistant_message_count)

    def send_file(self, file_path: str, prompt: str = None) -> str:
        resolved_path = Path(file_path).expanduser().resolve()
        if not resolved_path.exists():
            raise FileNotFoundError(f"文件不存在: {resolved_path}")
        if not resolved_path.is_file():
            raise ValueError(f"不是普通文件: {resolved_path}")

        try:
            send_kwargs = self._as_kwargs(self.adapter.before_file_send(resolved_path, prompt=prompt))
            previous_assistant_message_count = send_kwargs.pop("previous_assistant_message_count", None)
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
            self.logger.screenshot("send_file_after_send", full_page=False)
            self.logger.debug("已发送文件消息，等待 AI 回复...")
            return self.wait_for_reply(previous_assistant_message_count=previous_assistant_message_count)
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
        headless: bool = False,
        timeout: int = 120000,
        logger=None,
        user_data_dir: str = None,
        storage_state_path: str = None,
    ):
        default_storage_state_path = self.DEFAULT_STORAGE_STATE_PATH
        config = BrowserClientConfig(
            headless=headless,
            timeout=timeout,
            user_data_dir=Path(user_data_dir).expanduser().resolve() if user_data_dir else None,
            storage_state_path=(
                Path(storage_state_path).expanduser().resolve()
                if storage_state_path
                else default_storage_state_path
            ),
            browser_args=self.BROWSER_ARGS,
            init_script=self.INIT_SCRIPT,
        )
        self.config = config
        self.logger = logger or get_logger(
            self.LOGGER_NAME,
            log_dir=self.LOG_DIR,
            screenshot_dir=self.SCREENSHOT_DIR,
        )
        self.adapter = adapter
        self.session = BrowserSession(config, self.logger)
        self.adapter.bind(self.session, config, self.logger)
        self.workflow = BrowserConversationWorkflow(self.session, self.adapter, config, self.logger)

    @property
    def timeout(self) -> int:
        return self.config.timeout

    def start(self, url: str = None):
        self.session.start(url or self.adapter.default_url(), self.adapter)

    def close(self):
        self.session.close()

    def wait_for_reply(self) -> str:
        return self.workflow.wait_for_reply()

    def send_text(self, text: str, **options) -> str:
        return self.workflow.send_text(text, **options)

    def send_file(self, file_path: str, prompt: str = None) -> str:
        return self.workflow.send_file(file_path, prompt=prompt)
