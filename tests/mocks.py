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

    def evaluate(self, script):
        self.scripts.append(script)
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
