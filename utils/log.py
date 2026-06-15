# utils.py
import os
import sys
import time
from datetime import datetime
from typing import Optional

from playwright.sync_api import Page


class Logger:
    """
    简单的日志记录类，支持控制台输出和文件记录，以及 Playwright 页面截图。
    """

    def __init__(
        self,
        name: str = "CheapClaw",
        log_dir: str = "logs",
        screenshot_dir: str = "screenshots",
        console: bool = False,
        log_level: str = None,
    ):
        """
        初始化 Logger。
        :param name: 日志记录器名称（用于控制台/文件标识）
        :param log_dir: 日志文件存储目录
        :param screenshot_dir: 截图文件存储目录
        :param console: 是否同步输出到终端
        :param log_level: 日志级别，默认读取 CHEAPCLAW_LOG_LEVEL，debug 时才保存截图
        """
        self.name = name
        self.log_dir = log_dir
        self.screenshot_dir = screenshot_dir
        self.console = console
        self.log_level = (log_level or os.getenv("CHEAPCLAW_LOG_LEVEL", "info")).lower()
        self._ensure_dirs()
        self._page: Optional[Page] = None

    def _ensure_dirs(self):
        """确保日志目录和截图目录存在"""
        for d in [self.log_dir, self.screenshot_dir]:
            if not os.path.exists(d):
                os.makedirs(d, exist_ok=True)

    def _log(self, level: str, msg: str, *args, **kwargs):
        """内部日志记录方法，默认写入文件，可选同步输出到终端。"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_msg = f"[{timestamp}] [{level}] [{self.name}] {msg}"
        if self.console:
            print(formatted_msg, *args, **kwargs)
        log_file = os.path.join(self.log_dir, f"{datetime.now().strftime('%Y%m%d')}.log")
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(formatted_msg + "\n")
        except Exception as e:
            print(f"[ERROR] Failed to write log file: {e}", file=sys.stderr)

    def info(self, msg: str, *args, **kwargs):
        self._log("INFO", msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        self._log("WARNING", msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        self._log("ERROR", msg, *args, **kwargs)

    def debug(self, msg: str, *args, **kwargs):
        self._log("DEBUG", msg, *args, **kwargs)

    def attach_page(self, page: Page):
        """
        绑定 Playwright Page 对象，以便后续截图。
        :param page: Playwright 的 Page 实例
        """
        self._page = page

    def screenshot(self, name: str = None, full_page: bool = True):
        """
        对当前绑定的页面进行截图。
        :param name: 截图文件名（不含扩展名），默认使用时间戳
        :param full_page: 是否截取整个页面（滚动截图）
        :return: 截图文件路径，如果未绑定 page 则返回 None
        """
        if self.log_level != "debug":
            return None
        if self._page is None:
            self.error("No Page attached. Call attach_page(page) first.")
            return None
        if name is None:
            name = f"screenshot_{int(time.time())}"
        # 确保文件名安全（移除路径分隔符等）
        safe_name = "".join(c for c in name if c.isalnum() or c in "._-")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_name}_{timestamp}.png"
        filepath = os.path.join(self.screenshot_dir, filename)
        try:
            self._page.screenshot(path=filepath, full_page=full_page)
            self.info(f"Screenshot saved: {filepath}")
            return filepath
        except Exception as e:
            self.error(f"Failed to take screenshot: {e}")
            return None


_default_logger = None

def get_logger(
    name: str = "Logger",
    log_dir: str = "logs",
    screenshot_dir: str = "screenshots",
    console: bool = False,
    log_level: str = None,
) -> Logger:
    global _default_logger
    if _default_logger is None:
        _default_logger = Logger(name, log_dir, screenshot_dir, console=console, log_level=log_level)
    return _default_logger
