# qwen.py
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import sync_playwright
from utils import get_logger

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
        """等待 AI 回复完成并返回回复文本，兼容偶发的双回复评测 UI。"""
        deadline = time.time() + self.timeout / 1000
        last_reply = ""

        while time.time() < deadline:
            ab_reply = self._try_handle_reply_preference_ui()
            if ab_reply:
                return ab_reply

            reply = self._latest_reply_text()
            if reply and reply != last_reply:
                last_reply = reply
                self.logger.debug(f"检测到回复更新，当前长度 {len(reply)}")

            if reply and self._is_reply_completion_visible() and not self._is_generation_in_progress():
                self.logger.debug(f"回复完成，长度 {len(reply)}")
                return reply

            self.page.wait_for_timeout(500)

        self.logger.screenshot("wait_for_reply_timeout", full_page=True)
        if last_reply:
            self.logger.warning(f"等待回复完成超时，返回已捕获回复，长度 {len(last_reply)}")
            return last_reply
        raise TimeoutError("等待 AI 回复超时")

    def _latest_reply_text(self) -> str:
        try:
            reply_locator = self.page.locator(
                ".qwen-chat-message-assistant:last-child .response-message-content"
            )
            if reply_locator.count() == 0:
                return ""
            return reply_locator.last.inner_text(timeout=1000).strip()
        except Exception:
            return ""

    def _is_reply_completion_visible(self) -> bool:
        try:
            return self.page.locator(".qwen-chat-message-assistant:last-child .message-hoc-container").count() > 0
        except Exception:
            return False

    def _is_generation_in_progress(self) -> bool:
        try:
            return bool(self.page.evaluate(
                r"""
                () => {
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
                    const attrText = (el) => [
                        el.getAttribute('aria-label'),
                        el.getAttribute('title'),
                        el.getAttribute('data-testid'),
                    ].filter(Boolean).join(' ');
                    const buttons = Array.from(document.querySelectorAll('button, [role="button"], .send-button'))
                        .filter(visible);
                    return buttons.some((button) => {
                        const labels = `${textOf(button)} ${attrText(button)}`;
                        const hrefs = Array.from(button.querySelectorAll('use')).map((use) =>
                            use.getAttribute('href') || use.getAttribute('xlink:href') || ''
                        ).join(' ');
                        const merged = `${labels} ${hrefs}`;
                        return /停止|终止|stop|pause|icon-stop|icon-line-stop|square/i.test(merged) &&
                            !/send|发送|arrow-up|icon-send/i.test(merged);
                    });
                }
                """
            ))
        except Exception:
            return False

    def _try_handle_reply_preference_ui(self) -> str:
        """处理 Qwen 偶发的“您更喜欢哪个回复”双回复评测 UI，默认选择第一个回复。"""
        try:
            if self.page.locator("text=您更喜欢哪个回复").count() == 0:
                return ""

            self.logger.warning("检测到双回复评测 UI，默认选择第一个回复")
            self.logger.screenshot("reply_preference_ui", full_page=True)
            result = self.page.evaluate(
                r"""
                () => {
                    const textOf = (el) => (el && (el.innerText || el.textContent) || '').replace(/\s+/g, ' ').trim();
                    const cards = Array.from(document.querySelectorAll('div')).filter((el) => {
                        const text = textOf(el);
                        return /^回复\s*\d+/.test(text) && text.includes('我更喜欢这个回复');
                    });
                    const firstCard = cards[0];
                    if (!firstCard) return {reply: '', clicked: false};

                    const clone = firstCard.cloneNode(true);
                    Array.from(clone.querySelectorAll('button, [role="button"]')).forEach((el) => el.remove());
                    const reply = textOf(clone)
                        .replace(/^回复\s*1\s*/, '')
                        .replace(/已经完成思考\s*>?/g, '')
                        .replace(/我更喜欢这个回复/g, '')
                        .trim();

                    const buttons = Array.from(firstCard.querySelectorAll('button, [role="button"]'));
                    const preferButton = buttons.find((el) => textOf(el).includes('我更喜欢这个回复'));
                    if (preferButton) {
                        preferButton.click();
                        return {reply, clicked: true};
                    }
                    return {reply, clicked: false};
                }
                """
            )
            reply = (result or {}).get("reply", "").strip()
            if not (result or {}).get("clicked"):
                self.logger.warning("未能点击第一个偏好回复按钮")
            self.page.wait_for_timeout(1000)
            return reply
        except Exception as e:
            self.logger.debug(f"处理双回复评测 UI 失败: {type(e).__name__}: {e}")
            return ""

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
        previous_user_message_count = self._user_message_count()
        self._send_current_message(previous_user_message_count=previous_user_message_count)
        self.logger.debug("消息已发送，等待 AI 回复...")
        return self.wait_for_reply()

    def send_file(self, file_path: str, prompt: str = None) -> str:
        """
        通过上传文件发送长文本内容。
        :param file_path: 本地文件路径
        :param prompt: 可选，额外补充的指令文本（如“请总结该文件”），将在上传后填入输入框
        :return: AI 回复的文本
        """
        resolved_path = Path(file_path).expanduser().resolve()
        if not resolved_path.exists():
            raise FileNotFoundError(f"文件不存在: {resolved_path}")
        if not resolved_path.is_file():
            raise ValueError(f"不是普通文件: {resolved_path}")

        try:
            self._try_close_guidance()
            previous_user_message_count = self._user_message_count()

            self.logger.screenshot("send_file_before_upload", full_page=False)
            self._upload_file(resolved_path)
            self.logger.info(f"已上传文件: {resolved_path}")

            self._wait_for_uploaded_file_card(resolved_path)
            self.logger.info("文件已显示在输入框附件卡片中")
            self.logger.screenshot("send_file_after_upload_card", full_page=False)
            self._wait_until_sendable()

            had_text = bool(prompt)
            if prompt:
                textarea = self.page.locator(".message-input-textarea")
                textarea.wait_for(state="visible", timeout=self.timeout)
                textarea.fill(prompt)
                self._wait_until_sendable()
                self.logger.screenshot("send_file_after_prompt", full_page=False)

            self.logger.screenshot("send_file_before_send", full_page=False)
            self._send_current_message(
                previous_user_message_count=previous_user_message_count,
                prefer_button=True,
                had_text=had_text,
            )
            self.logger.screenshot("send_file_after_send", full_page=False)
            self.logger.debug("已发送文件消息，等待 AI 回复...")
            return self.wait_for_reply()
        except Exception:
            self.logger.screenshot("send_file_error", full_page=True)
            raise

    def _upload_file(self, file_path: Path) -> None:
        """按 Qwen 前端真实交互打开上传菜单并选择文件，失败时回退隐藏 input。"""
        try:
            self._open_upload_menu()
            upload_item = self._upload_attachment_item()
            upload_item.wait_for(state="visible", timeout=5000)
            self.logger.screenshot("send_file_upload_menu_opened", full_page=False)
            with self.page.expect_file_chooser(timeout=5000) as chooser_info:
                upload_item.click(timeout=5000)
            chooser_info.value.set_files(str(file_path))
            return
        except Exception as e:
            self.logger.debug(f"上传菜单方式失败，回退隐藏 input: {type(e).__name__}: {e}")
            self.logger.screenshot("send_file_upload_menu_failed", full_page=False)
            self._upload_file_by_input(file_path)

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
        file_input.wait_for(state="attached", timeout=self.timeout)
        file_input.set_input_files(str(file_path))

    def _wait_for_uploaded_file_card(self, file_path: Path) -> None:
        """等待上传后的文件卡片出现在输入框附件区域。"""
        stem = file_path.stem
        suffix = file_path.suffix
        self.page.wait_for_selector(
            ".message-input-column-file .file-card-list .fileitem-btn",
            state="visible",
            timeout=self.timeout,
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
            timeout=self.timeout,
        )

    def _wait_until_sendable(self, timeout: int = None) -> None:
        """等待发送按钮可用，表示当前输入或附件可以提交。"""
        wait_timeout = timeout or self.timeout
        self.page.wait_for_selector(
            "button.send-button:not([disabled]), .send-button:not([disabled])",
            state="visible",
            timeout=wait_timeout,
        )

    def _user_message_count(self) -> int:
        """返回当前页面中的用户消息数量。"""
        return self.page.locator(".qwen-chat-message-user").count()

    def _send_current_message(
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
                    self._wait_until_sendable(timeout=5000)
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
    import argparse

    parser = argparse.ArgumentParser(description="验证 QwenClient.send_file 文件上传能力")
    parser.add_argument("file", nargs="?", help="待上传文件；不传则自动生成 debug/send_file_sample.txt")
    parser.add_argument("--prompt", default="请总结这个文件，并说明你是否成功读取到了文件内容。")
    parser.add_argument("--file-only", action="store_true", help="不填 prompt，仅上传文件并发送")
    parser.add_argument("--headed", action="store_true", help="使用有头浏览器，便于观察上传过程")
    args = parser.parse_args()

    if args.file:
        test_file = Path(args.file).expanduser().resolve()
    else:
        test_file = Path(PROJECT_ROOT) / "debug" / "send_file_sample.txt"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(
            "这是一个用于验证 QwenClient.send_file 的测试文件。\n"
            "如果你能读到这段文字，请在回复中提到 send_file_sample。\n",
            encoding="utf-8",
        )

    prompt = None if args.file_only else args.prompt
    client = QwenClient(headless=not args.headed)
    try:
        client.start()
        print(f"上传文件: {test_file}")
        print(f"Prompt: {prompt or '(file-only)'}")
        reply = client.send_file(str(test_file), prompt=prompt)
        print("回复：", reply)
    finally:
        client.close()
