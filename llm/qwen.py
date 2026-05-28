# qwen.py
import os
import time

from playwright.sync_api import sync_playwright
from utils import get_logger

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
STORAGE_STATE_PATH = os.path.join(CONFIG_DIR, "storage_state.json")

BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
]


class QwenClient:
    """
    通义千问网页版自动化客户端（基于持久化会话）。
    首次使用时需手动登录一次保存状态，后续自动复用。
    """

    def __init__(self, headless: bool = False, timeout: int = 120000, logger=None, user_data_dir: str = None, storage_state_path: str = None):
        """
        初始化客户端。
        :param headless: 是否无头模式
        :param timeout: 默认超时时间（毫秒）
        :param user_data_dir: Playwright 持久化用户数据目录，适合在有界面机器上完成登录
        :param storage_state_path: 可迁移登录态 JSON，适合复制到无界面开发机复用
        """
        self.headless = headless
        self.timeout = timeout
        self.user_data_dir = os.path.abspath(user_data_dir) if user_data_dir else None
        self.storage_state_path = os.path.abspath(storage_state_path or STORAGE_STATE_PATH)
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.logger = logger or get_logger("QwenClient", log_dir="./debug", screenshot_dir="./debug/screenshots")

    def start(self, url: str = "https://chat.qwen.ai/"):
        """
        启动浏览器并加载保存的登录状态（如果存在）。
        有界面机器建议传入 user_data_dir 完成登录，并导出 storage_state.json；
        无界面开发机建议直接复用 storage_state.json。
        """
        self.playwright = sync_playwright().start()
        storage_state_path = self._resolve_storage_state_path()

        if self.user_data_dir:
            os.makedirs(self.user_data_dir, exist_ok=True)
            self.logger.info(f"使用持久化用户数据目录: {self.user_data_dir}")
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=self.headless,
                args=BROWSER_ARGS,
            )
            self.browser = self.context
            self.logger.info("已加载持久化用户数据目录。")
        elif storage_state_path:
            self.browser = self.playwright.chromium.launch(
                headless=self.headless,
                args=BROWSER_ARGS,
            )
            self.context = self.browser.new_context(storage_state=storage_state_path)
            self.logger.info(f"已加载 storage_state 登录状态: {storage_state_path}")
        else:
            self.browser = self.playwright.chromium.launch(
                headless=self.headless,
                args=BROWSER_ARGS,
            )
            self.context = self.browser.new_context()
            self.logger.info("未找到登录状态，以未登录模式启动。")

        self.page = self.context.new_page()
        self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        self.logger.attach_page(self.page)
        self.logger.info("Starting browser...")
        self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)

        if not self._is_logged_in():
            if self.headless:
                raise RuntimeError(
                    f"当前是 headless 模式且未登录。请先在有界面机器导出 {self.storage_state_path} 后复制到开发机。"
                )
            self.logger.warning("登录状态已过期或未登录，请在浏览器窗口中手动登录...")
            self._wait_for_login()
            self._save_storage_state()
            self.logger.info("检测到登录成功并保存登录状态")

        self.page.wait_for_selector(".message-input-textarea", timeout=self.timeout)
        self.page.wait_for_timeout(1000)
        self._inject_overlay_auto_dismiss()
        self.logger.debug("Page loaded")

    def close(self):
        """关闭浏览器和会话。"""
        if self.context:
            self.context.close()
        if self.browser and self.browser is not self.context:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        self.logger.info("Browser closed")

    def wait_for_reply(self) -> str:
        """等待 AI 回复完成并返回回复文本。"""
        self.page.wait_for_selector(
            ".qwen-chat-message-assistant:last-child .message-hoc-container",
            timeout=self.timeout,
        )
        reply_locator = self.page.locator(
            ".qwen-chat-message-assistant:last-child .response-message-content"
        )
        reply = reply_locator.last.inner_text().strip()
        self.logger.debug("检测到操作容器，回复完成")
        return reply

    def send_text(self, text: str, auto_remove_limit: bool = True, auto_close_guidance: bool = True) -> str:
        """
        通过输入框发送文本消息。
        :param text: 要发送的文本内容
        :param auto_remove_limit: 是否自动移除输入框的 maxlength 限制（默认 True）
        :param auto_close_guidance: 是否自动关闭首页引导/示例区域（默认 True）
        :return: AI 回复的文本
        """
        textarea = self.page.locator(".message-input-textarea")
        textarea.wait_for(state="visible", timeout=self.timeout)

        if auto_close_guidance:
            self._try_close_guidance()
            textarea = self.page.locator(".message-input-textarea")
            textarea.wait_for(state="visible", timeout=self.timeout)

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
        self._send_current_message()
        self.logger.debug("消息已发送，等待 AI 回复...")
        return self.wait_for_reply()

    def send_file(self, file_path: str, prompt: str = None) -> str:
        """
        通过上传文件发送长文本内容。
        :param file_path: 本地文件路径
        :param prompt: 可选，额外补充的指令文本（如“请总结该文件”），将在上传后填入输入框
        :return: AI 回复的文本
        """
        file_input = self.page.locator("#filesUpload")
        file_input.wait_for(state="attached", timeout=self.timeout)
        file_input.set_input_files(file_path)
        self.logger.info(f"已上传文件: {file_path}")

        try:
            self.page.wait_for_selector(".ant-upload-list-item", timeout=15000)
            self.logger.info("文件已显示在附件列表中")
        except Exception:
            self.logger.info("未检测到文件列表元素，等待5秒...")
            self.page.wait_for_timeout(5000)

        if prompt:
            textarea = self.page.locator(".message-input-textarea")
            textarea.fill(prompt)

        self._send_current_message()
        self.logger.debug("已发送文件消息，等待 AI 回复...")
        return self.wait_for_reply()

    def _send_current_message(self):
        textarea = self.page.locator(".message-input-textarea")
        send_button = self.page.locator("button.send-button")

        try:
            textarea.press("Enter")
            if self._is_sent():
                return
        except Exception as e:
            self.logger.debug(f"Enter 发送异常: {type(e).__name__}: {e}")

        try:
            send_button.wait_for(state="visible", timeout=5000)
            send_button.click(timeout=5000)
            if self._is_sent():
                return
        except Exception as e:
            self.logger.debug(f"按钮发送异常: {type(e).__name__}: {e}")

        raise RuntimeError("发送失败：输入框未被清空，未确认消息已发出")

    def _is_sent(self) -> bool:
        """Qwen 发送成功后会自动清空输入框。"""
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

    def _resolve_storage_state_path(self) -> str | None:
        if os.path.exists(self.storage_state_path):
            return self.storage_state_path
        return None

    def _save_storage_state(self):
        os.makedirs(os.path.dirname(self.storage_state_path), exist_ok=True)
        try:
            self.context.storage_state(path=self.storage_state_path, indexed_db=True)
        except TypeError:
            self.context.storage_state(path=self.storage_state_path)
        self.logger.info(f"已保存 storage_state: {self.storage_state_path}")

    def _wait_for_login(self):
        deadline = time.time() + self.timeout / 1000
        while time.time() < deadline:
            if self._is_logged_in():
                return
            self.page.wait_for_timeout(1000)
        raise TimeoutError("等待手动登录超时")

    def _is_logged_in(self) -> bool:
        """检查当前页面是否已登录。"""
        try:
            current_url = self.page.url.lower()
            if any(keyword in current_url for keyword in ["passport", "login", "auth"]):
                self.logger.warning(f"检测到重定向至登录页: {current_url}")
                return False

            state = self.page.evaluate(
                r"""
                async () => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 &&
                            style.display !== 'none' &&
                            style.visibility !== 'hidden' &&
                            style.opacity !== '0';
                    };
                    const textOf = (el) => (el && (el.innerText || el.textContent) || '').replace(/\s+/g, ' ').trim();
                    const authButtons = Array.from(document.querySelectorAll('button, a')).filter((el) => {
                        const text = textOf(el);
                        return visible(el) && /^(Log in|Sign up|登录|注册)$/i.test(text);
                    }).map((el) => textOf(el));
                    let authStatus = null;
                    try {
                        const resp = await fetch('/api/v1/auths/', {credentials: 'include'});
                        authStatus = resp.status;
                    } catch (error) {
                        authStatus = `error:${error && error.message ? error.message : error}`;
                    }
                    return {authButtons, authStatus};
                }
                """
            )
            self.logger.debug(f"登录状态检查: {state}")
            return state.get("authStatus") == 200 and not state.get("authButtons")
        except Exception as e:
            self.logger.warning(f"登录状态检查失败: {type(e).__name__}: {e}")
            return False

    def _inject_overlay_auto_dismiss(self):
        """自动关闭可能遮挡输入区的弹窗。"""
        self.page.evaluate(
            r"""
            () => {
                if (window.__qwenOverlayObserver) return;

                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 &&
                        style.display !== 'none' &&
                        style.visibility !== 'hidden' &&
                        style.opacity !== '0';
                };
                const textOf = (el) => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                const clickByText = (root, patterns) => {
                    for (const el of root.querySelectorAll('button,[role="button"],a')) {
                        if (!visible(el)) continue;
                        const text = textOf(el);
                        if (patterns.some((pattern) => pattern.test(text))) {
                            el.click();
                            return true;
                        }
                    }
                    return false;
                };
                const dismiss = () => {
                    const overlays = document.querySelectorAll(
                        '.qwen-modal-overlay, .ant-modal-root, .ant-modal-wrap, .ant-modal, [role="dialog"], [aria-modal="true"]'
                    );
                    let sawOverlay = false;
                    for (const overlay of overlays) {
                        if (!visible(overlay)) continue;
                        sawOverlay = true;
                        if (clickByText(overlay, [/继续/, /稍后/, /暂不/, /我知道/, /知道了/, /关闭/, /close/i, /cancel/i])) {
                            return 'dismissed';
                        }
                        const dismissButton = overlay.querySelector('.ant-modal-close, .close, .qwen-modal-close');
                        if (visible(dismissButton)) {
                            dismissButton.click();
                            return 'dismissed';
                        }
                    }
                    return sawOverlay ? 'blocked' : 'none';
                };

                dismiss();
                window.__qwenOverlayObserver = new MutationObserver(() => {
                    if (dismiss() === 'blocked') {
                        document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
                    }
                });
                window.__qwenOverlayObserver.observe(document.body, {childList: true, subtree: true});
            }
            """
        )
        self.logger.debug("弹窗自动关闭 hook 已注入")


if __name__ == "__main__":
    client = QwenClient(headless=True)
    try:
        client.start()
        reply = client.send_text("你是谁？")
        print("回复：", reply)
    finally:
        client.close()
