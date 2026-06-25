class DummyLogger:
    def __init__(self):
        self.debug_messages = []
        self.warning_messages = []

    def debug(self, message):
        self.last_debug = message
        self.debug_messages.append(message)

    def warning(self, message):
        self.last_warning = message
        self.warning_messages.append(message)

    def screenshot(self, *args, **kwargs):
        self.last_screenshot = (args, kwargs)


class DummyConfig:
    timeout = 1000


class FakeSelectorConfig:
    """测试用选择器配置，模拟 SelectorConfig 的行为。"""

    def __init__(self, **overrides):
        # 核心交互
        self.composer = "textarea.composer"
        self.send_button = "button.send:not([disabled])"
        self.sendable_fallback = None
        # 上传相关
        self.upload_button = None
        self.upload_menu_trigger = None
        self.upload_menu_item = None
        self.upload_file_input = None
        # 回复区域检测
        self.reply_content = ".reply"
        self.reply_citation = ".cite"
        self.user_message = ".user-msg"
        self.file_card = ".file-card"
        # 对话管理
        self.conversation_item_link = None
        self.conversation_menu_button = None
        self.conversation_delete_option = None
        self.confirm_dialog_delete_button = None
        # 生成状态
        self.generation_stop_keywords = ["stop", "停止"]
        # 登录检测
        self.login_url_keywords = ["/sign_in", "/login", "auth"]
        # UI 辅助
        self.guidance_close_button = None
        # 文件卡片
        self.upload_file_card_list = None
        self.upload_file_card_item = None
        self.upload_file_card_name = None
        self.upload_file_card_ext = None
        # 允许覆盖
        for key, value in overrides.items():
            setattr(self, key, value)


class FakeSession:
    def __init__(self, page):
        self.page = page


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector
        self.first = self

    def wait_for(self, **kwargs):
        self.page.locator_waits.append((self.selector, kwargs))

    def fill(self, text):
        self.page.fills.append((self.selector, text))
        if self.page.fill_exc:
            raise RuntimeError("fill failed")

    def click(self, **kwargs):
        self.page.clicks.append((self.selector, kwargs))

    def press(self, key):
        self.page.presses.append((self.selector, key))


class FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def insert_text(self, text):
        self.page.inserted_texts.append(text)


class FakePage:
    def __init__(
        self,
        url="https://chat.deepseek.com",
        values=None,
        exc=False,
        wait_selector_failures=None,
        wait_function_failures=None,
        fill_exc=False,
    ):
        self.url = url
        self.values = values or []
        self.exc = exc
        self.fill_exc = fill_exc
        self.wait_selector_failures = set(wait_selector_failures or [])
        self.wait_function_failures = set(wait_function_failures or [])
        self.scripts = []
        self.waited_selectors = []
        self.waited_functions = []
        self.waited_timeouts = []
        self.locator_waits = []
        self.clicks = []
        self.presses = []
        self.fills = []
        self.inserted_texts = []
        self.keyboard = FakeKeyboard(self)

    def evaluate(self, script, arg=None):
        appended = (script, arg) if arg is not None else script
        self.scripts.append(appended)
        if self.exc:
            raise RuntimeError("evaluate failed")
        if self.values:
            value = self.values.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        return None

    def wait_for_selector(self, selector, **kwargs):
        self.waited_selectors.append((selector, kwargs))
        if selector in self.wait_selector_failures:
            raise RuntimeError("selector failed")

    def wait_for_function(self, script, **kwargs):
        self.waited_functions.append((script, kwargs))
        if script in self.wait_function_failures:
            raise RuntimeError("function failed")

    def wait_for_timeout(self, timeout):
        self.waited_timeouts.append(timeout)

    def locator(self, selector):
        return FakeLocator(self, selector)
