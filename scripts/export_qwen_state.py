#!/usr/bin/env python3
"""Export Qwen login state for reuse on a headless machine.

Run this on a machine with a visible browser. The script opens Qwen, waits for you
to log in manually, then writes Playwright storage state to config/storage_state.json.
"""

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

DEFAULT_OUTPUT = Path.cwd() / "config" / "storage_state.json"
DEFAULT_USER_DATA_DIR = Path.cwd() / "config" / "qwen_login_profile"
QWEN_URL = "https://chat.qwen.ai/"


def parse_args():
    parser = argparse.ArgumentParser(description="Export Qwen Playwright storage_state.json after manual login.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output storage_state JSON path.")
    parser.add_argument("--user-data-dir", default=str(DEFAULT_USER_DATA_DIR), help="Temporary browser profile path for the export flow only.")
    parser.add_argument("--timeout", type=int, default=300, help="Seconds to wait for manual login.")
    parser.add_argument("--browser", default=None, help="Optional browser executable path, e.g. /usr/bin/google-chrome.")
    return parser.parse_args()


def check_login(page):
    return page.evaluate(
        """
        async () => {
            const textOf = (el) => (el && (el.innerText || el.textContent) || '').replace(/\s+/g, ' ').trim();
            const visible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 &&
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    style.opacity !== '0';
            };
            let authStatus = null;
            try {
                const resp = await fetch('/api/v1/auths/', {credentials: 'include'});
                authStatus = resp.status;
            } catch (error) {
                authStatus = `error:${error && error.message ? error.message : error}`;
            }
            return {
                authStatus,
                href: location.href,
                hasTextarea: !!document.querySelector('.message-input-textarea'),
                authButtons: Array.from(document.querySelectorAll('button, a'))
                    .filter((el) => visible(el) && /^(Log in|Sign up|登录|注册)$/i.test(textOf(el)))
                    .map((el) => textOf(el)),
            };
        }
        """
    )


def summarize_storage_state(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    cookies = data.get("cookies") or []
    origins = data.get("origins") or []
    print(f"cookies_count={len(cookies)}")
    print(f"cookie_domains={sorted({cookie.get('domain', '') for cookie in cookies})}")
    print(f"cookie_names={sorted({cookie.get('name', '') for cookie in cookies})}")
    print(f"origins={[origin.get('origin') for origin in origins]}")
    for origin in origins:
        if "qwen" in (origin.get("origin") or ""):
            local_storage = origin.get("localStorage") or []
            print(f"qwen_localStorage_count={len(local_storage)}")
            print(f"qwen_localStorage_keys={sorted(item.get('name', '') for item in local_storage)}")


def main():
    args = parse_args()
    output = Path(args.output).expanduser().resolve()
    user_data_dir = Path(args.user_data_dir).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    user_data_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        launch_kwargs = {
            "user_data_dir": str(user_data_dir),
            "headless": False,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if args.browser:
            launch_kwargs["executable_path"] = args.browser

        context = playwright.chromium.launch_persistent_context(**launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        page.goto(QWEN_URL, wait_until="domcontentloaded", timeout=120_000)

        print("请在打开的浏览器中完成 Qwen 登录，可使用密码、GitHub、二维码等任意网页登录方式。")
        print(f"登录成功后会导出到: {output}")
        print(f"等待登录，超时时间 {args.timeout} 秒...")

        deadline = time.time() + args.timeout
        last_status = None
        while time.time() < deadline:
            status = check_login(page)
            if status != last_status:
                print(f"login_check={status}")
                last_status = status
            if status.get("authStatus") == 200:
                try:
                    context.storage_state(path=str(output), indexed_db=True)
                    indexed_db = True
                except TypeError:
                    context.storage_state(path=str(output))
                    indexed_db = False
                print(f"已导出: {output}")
                print(f"indexed_db={indexed_db}")
                summarize_storage_state(output)
                context.close()
                return
            page.wait_for_timeout(1000)

        context.close()
        raise TimeoutError(f"等待登录超时，最后状态: {last_status}")


if __name__ == "__main__":
    main()
