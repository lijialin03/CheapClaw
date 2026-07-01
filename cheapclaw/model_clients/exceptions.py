class CheapClawError(Exception):
    """所有 CheapClaw 异常的基类。"""

    pass


class BrowserError(CheapClawError):
    """浏览器相关异常。"""

    pass


class LoginExpiredError(BrowserError):
    """登录会话已过期，需要重新导出登录态。"""

    pass


class GenerationFailureError(BrowserError):
    """模型返回错误（如额度用完、服务不可用等）。"""

    pass


class NetworkError(BrowserError):
    """网络连接异常。"""

    pass


class SelectorNotFoundError(BrowserError):
    """关键 DOM 选择器在当前页面中未找到（可能网站已改版）。"""

    pass
