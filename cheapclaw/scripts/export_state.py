"""Export web model login state for reuse on a headless machine.

Run this on a machine with a visible browser. The script opens the selected
model frontend, waits for you to log in manually, then writes Playwright storage
state to the model-specific config/storage_state_<model>.json file.
"""

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from playwright.sync_api import sync_playwright

QWEN_URL = "https://chat.qwen.ai/"
DEEPSEEK_URL = "https://chat.deepseek.com"

QWEN_LOGIN_CHECK_JS = r"""
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

DEEPSEEK_LOGIN_CHECK_JS = r"""
() => {
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
    const href = location.href;
    const isSignInUrl = /\/sign_in|\/signin|\/login|passport|auth/i.test(href);
    const hasComposer = Array.from(document.querySelectorAll("textarea, [contenteditable='true'], div[role='textbox']"))
        .some(visible);
    const authButtons = Array.from(document.querySelectorAll('button, a, [role="button"]'))
        .filter((el) => visible(el) && /^(Sign in|Sign up|Log in|Login|登录|注册|登入|立即登录)$/i.test(textOf(el)))
        .map((el) => textOf(el));
    return {href, isSignInUrl, hasComposer, authButtons};
}
"""


@dataclass(frozen=True)
class ExportSpec:
    model: str
    display_name: str
    url: str
    default_output: Path
    default_user_data_dir: Path
    login_message: str
    timeout_error_prefix: str
    origin_keyword: str
    local_storage_label: str
    check_login: Callable
    is_logged_in: Callable[[dict], bool]


def _check_qwen_login(page):
    return page.evaluate(QWEN_LOGIN_CHECK_JS)


def _check_deepseek_login(page):
    return page.evaluate(DEEPSEEK_LOGIN_CHECK_JS)


def _is_qwen_logged_in(status: dict) -> bool:
    return status.get("authStatus") == 200


def _is_deepseek_logged_in(status: dict) -> bool:
    return (
        not status.get("isSignInUrl")
        and status.get("hasComposer")
        and not status.get("authButtons")
    )


EXPORT_MODELS = {
    "qwen": ExportSpec(
        model="qwen",
        display_name="Qwen",
        url=QWEN_URL,
        default_output=Path.cwd() / "config" / "storage_state_qwen.json",
        default_user_data_dir=Path.cwd() / "config" / "login_profile_qwen",
        login_message="请在打开的浏览器中完成 Qwen 登录，可使用密码、GitHub、二维码等任意网页登录方式。",
        timeout_error_prefix="等待 Qwen 登录超时",
        origin_keyword="qwen",
        local_storage_label="qwen",
        check_login=_check_qwen_login,
        is_logged_in=_is_qwen_logged_in,
    ),
    "deepseek": ExportSpec(
        model="deepseek",
        display_name="DeepSeek",
        url=DEEPSEEK_URL,
        default_output=Path.cwd() / "config" / "storage_state_ds.json",
        default_user_data_dir=Path.cwd() / "config" / "login_profile_ds",
        login_message="请在打开的浏览器中完成 DeepSeek 登录；页面可能会重定向到 /sign_in。",
        timeout_error_prefix="等待 DeepSeek 登录超时",
        origin_keyword="deepseek",
        local_storage_label="deepseek",
        check_login=_check_deepseek_login,
        is_logged_in=_is_deepseek_logged_in,
    ),
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export Playwright storage state JSON after manual model login."
    )
    parser.add_argument(
        "--model",
        choices=sorted(EXPORT_MODELS),
        default="qwen",
        help="Model frontend to export login state for.",
    )
    parser.add_argument(
        "--output", default=None, help="Output storage state JSON path."
    )
    parser.add_argument(
        "--user-data-dir",
        default=None,
        help="Temporary browser profile path for the export flow only.",
    )
    parser.add_argument(
        "--timeout", type=int, default=300, help="Seconds to wait for manual login."
    )
    parser.add_argument(
        "--browser",
        default=None,
        help="Optional browser executable path, e.g. /usr/bin/google-chrome.",
    )
    return parser.parse_args()


def summarize_storage_state(path, spec: ExportSpec):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cookies = data.get("cookies") or []
    origins = data.get("origins") or []
    print(f"cookies_count={len(cookies)}")
    print(f"cookie_domains={sorted({cookie.get('domain', '') for cookie in cookies})}")
    print(f"cookie_names={sorted({cookie.get('name', '') for cookie in cookies})}")
    print(f"origins={[origin.get('origin') for origin in origins]}")
    for origin in origins:
        if spec.origin_keyword in (origin.get("origin") or ""):
            local_storage = origin.get("localStorage") or []
            print(f"{spec.local_storage_label}_localStorage_count={len(local_storage)}")
            print(
                f"{spec.local_storage_label}_localStorage_keys="
                f"{sorted(item.get('name', '') for item in local_storage)}"
            )


def main():
    args = parse_args()
    spec = EXPORT_MODELS[args.model]
    output = Path(args.output or spec.default_output).expanduser().resolve()
    user_data_dir = (
        Path(args.user_data_dir or spec.default_user_data_dir).expanduser().resolve()
    )
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
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )
            page.goto(spec.url, wait_until="domcontentloaded", timeout=120_000)

            print(spec.login_message)
            print(f"登录成功后会导出到: {output}")
            print(f"等待登录，超时时间 {args.timeout} 秒...")

            deadline = time.time() + args.timeout
            last_status = None
            while time.time() < deadline:
                status = spec.check_login(page)
                if status != last_status:
                    print(f"login_check={status}")
                    last_status = status
                if spec.is_logged_in(status):
                    try:
                        context.storage_state(path=str(output), indexed_db=True)
                        indexed_db = True
                    except TypeError:
                        context.storage_state(path=str(output))
                        indexed_db = False
                    print(f"已导出: {output}")
                    print(f"indexed_db={indexed_db}")
                    summarize_storage_state(output, spec)
                    return
                page.wait_for_timeout(1000)

            raise TimeoutError(f"{spec.timeout_error_prefix}，最后状态: {last_status}")
        finally:
            context.close()


if __name__ == "__main__":
    main()
