# 新增网页模型 Client 指南

CheapClaw 通过 Playwright 自动化网页操作来使用模型（而非 API），复用浏览器登录态，完成输入、发送、读取回复全流程。

核心原则：**站点差异写进 `BrowserFrontendAdapter` 子类 + `selectors.yaml`，通用流程不动基类。**

## 1. 架构概览

接入新模型时，开发者需要实现两类对象和一份配置：

- **XxxClient** — 继承 `BrowserModelClient`，只做简单配置（URL、存储路径、构造并注入 Adapter），代码量极少
- **XxxAdapter** — 继承 `BrowserFrontendAdapter`，实现站点差异，是接入工作的核心
- **selectors.yaml** — YAML 格式的 CSS 选择器配置，网站改版时只需修改此文件无需改代码

```text
                    BrowserModelClient ─── 通用流程（已实现）─────────────────
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
 BrowserSession  Workflow   BrowserFrontendAdapter  ←── 抽象基类
 浏览器生命周期   send_text       │           ▲
 登录态加载       send_file       │           │
                wait_for_reply    ▼           │
                               XxxAdapter ────┘  ←──★ 需要你实现
                               (JS 脚本 / 登录判断 / 发送钩子...)
                               + selectors.yaml ──★ 需要你配置
```

## 2. 接入位置

新增模型需要改动以下文件：

```text
model_clients/xxx/              # 新包：client.py + selectors.yaml + scripts/*.js
model_clients/__init__.py       # 导出 XxxClient
run.py                          # 注册 --model xxx
scripts/export_state.py         # 可选：注册登录态导出
tests/test_xxx_client.py        # 建议：单元测试
```

## 3. 选择器配置 (selectors.yaml)

选择器已从代码中外部化到 YAML 文件，基类通过 `SelectorConfig` 模型加载。创建 `model_clients/xxx/selectors.yaml`：

```yaml
# 核心交互
composer: 'textarea.input-area'
send_button: 'button.send-btn:not([disabled])'

# 回复区域检测
reply_content: '.assistant-message-content'
user_message: '[class*="user-message"]'

# 生成状态关键词
generation_stop_keywords:
  - stop
  - 停止

# 登录检测
login_url_keywords:
  - /login
  - /signin
```

完整字段列表见 `model_clients/selector_config.py` 中的 `SelectorConfig` 模型。必填字段：`composer`、`send_button`、`reply_content`。其余字段按站点需要可选填入。

在 `pyproject.toml` 中声明 package data 以包含 YAML：

```toml
[tool.setuptools.package-data]
"model_clients.xxx" = ["scripts/*.js", "selectors.yaml"]
```

## 4. 最小实现

### 4.1 创建包结构

```text
model_clients/xxx/
├── __init__.py                  # from .client import XxxClient
├── selectors.yaml               # CSS 选择器配置
├── client.py                    # XxxAdapter + XxxClient
└── scripts/
    ├── latest_reply.js          # 提取最新 assistant 回复文本
    ├── assistant_count.js       # 统计 assistant 消息节点数
    ├── generation_in_progress.js# 检测是否仍在生成中
    ├── login_state.js           # 登录状态检测
    ├── user_count.js            # 统计用户消息数
    └── composer_empty.js        # 检测输入框是否已清空(发送成功)
```

### 4.2 最小 client.py

```python
from pathlib import Path

from agent_core.config import BrowserConfig

from ..browser_base import BrowserFrontendAdapter, BrowserModelClient
from ..selector_config import SelectorConfig, load_selector_config

# ── 常量 ──

XXX_URL = "https://example.com"
STORAGE_STATE_PATH = Path.cwd() / "config" / "storage_state_xxx.json"
BROWSER_ARGS = ["--disable-blink-features=AutomationControlled"]


class XxxAdapter(BrowserFrontendAdapter):

    # ── 类属性 (JS 脚本声明) ──

    SEND_LOG_PREFIX = "Xxx "

    LATEST_REPLY_SCRIPT = "latest_reply.js"
    ASSISTANT_COUNT_SCRIPT = "assistant_count.js"
    GENERATION_IN_PROGRESS_SCRIPT = "generation_in_progress.js"
    USER_COUNT_SCRIPT = "user_count.js"
    COMPOSER_EMPTY_SCRIPT = "composer_empty.js"
    LOGIN_STATE_SCRIPT = "login_state.js"

    def __init__(self, selector_config: SelectorConfig | None = None):
        self.selector_config = selector_config or load_selector_config("xxx")

    # ── 抽象生命周期 ──

    def default_url(self) -> str:
        return XXX_URL

    def is_logged_in(self) -> bool:
        # JS 脚本现在接收选择器参数
        state = self.page.evaluate(
            self.load_js(self.LOGIN_STATE_SCRIPT), self._selector_args()
        ) or {}
        return bool(state.get("loggedIn"))

    def wait_until_ready(self) -> None:
        # 使用 self.selector_config.composer，不再直接引用常量
        self.page.wait_for_selector(
            self.selector_config.composer, timeout=self.config.timeout
        )

    # ── 文本发送 ──

    def before_text_send(self, text: str, **kwargs):
        composer = self._composer_locator()
        composer.wait_for(state="visible", timeout=self.config.timeout)
        previous_user_message_count = self._user_message_count()
        previous_assistant_message_count = self.assistant_message_count()
        composer.fill(text)
        return {
            "previous_user_message_count": previous_user_message_count,
            "previous_assistant_message_count": previous_assistant_message_count,
            "had_text": True,
        }


class XxxClient(BrowserModelClient):
    BROWSER_ARGS = BROWSER_ARGS
    LOGGER_NAME = "XxxClient"
    DISPLAY_NAME = "Xxx"
    DEFAULT_STORAGE_STATE_PATH = STORAGE_STATE_PATH

    def __init__(
        self,
        config: BrowserConfig | None = None,
        headless: bool | None = None,
        timeout: int | None = None,
        logger=None,
        storage_state_path: str | Path | None = None,
        cleanup_session: bool = False,
    ):
        super().__init__(
            adapter=XxxAdapter(),
            config=config,
            headless=headless,
            timeout=timeout,
            logger=logger,
            storage_state_path=storage_state_path,
            cleanup_session=cleanup_session,
        )
```

关键变化：
- 选择器不再用模块级常量，改为 `__init__` 中通过 `load_selector_config("xxx")` 加载
- `_composer_locator()` / `_send_button_locator()` 由基类实现，从 `self.selector_config` 取选择器
- `_selector_args()` 由基类提供，返回传给参数化 JS 的选择器字典

### 4.3 JS 脚本

基类通过配置的 JS 脚本名自动加载 `model_clients/xxx/scripts/<name>.js`。**JS 脚本已参数化，接收 `selectors` 对象作为参数。**

| 脚本文件 | 返回值 | 函数签名 | 说明 |
| --- | --- | --- | --- |
| `latest_reply.js` | `string` | `(selectors) => ...` | 最新 assistant 回复文本 |
| `assistant_count.js` | `int` | `(selectors) => ...` | 页面中 assistant 消息节点的数量 |
| `generation_in_progress.js` | `bool` | `(selectors) => ...` | 是否正在生成回复 |
| `user_count.js` | `int` | `(selectors) => ...` | 用户消息节点数量 |
| `composer_empty.js` | `bool` | `(selectors) => ...` | 输入框是否已清空 |
| `login_state.js` | `object` | `(selectors) => ...` | 登录状态信息 |

**参数化示例 — `latest_reply.js`：**

```javascript
(selectors) => {
    const nodes = Array.from(document.querySelectorAll(selectors.reply_content))
        .filter((el) => (el.innerText || el.textContent || '').trim());
    const node = nodes[nodes.length - 1];
    if (!node) return '';
    return (node.innerText || node.textContent || '').trim();
}
```

Python 端调用时基类自动传入 `self._selector_args()`，其中包含 `selectors.yaml` 中定义的所有选择器字段。`user_count_advanced.js` 等需要额外参数的脚本，基类会通过数组 `arg=[selectors, extraArg]` 传入。

基类已内置回复检测逻辑：`is_reply_complete()` 默认取反 `is_generation_in_progress()`，`send_current_message()` 通过 `selector_config` 中的 send_button 实现 Enter/按钮双路径发送。

### 4.4 可选扩展

在最小实现基础上，可以按需覆写以下方法组：

**可选生命周期钩子**
- `login_state_guidance()` — 向用户展示如何获取登录态
- `after_page_loaded()` — 页面 ready 后注入脚本、关闭弹窗
- `after_message_sent()` — 发送后记录会话 ID 等
- `cleanup_session()` — 退出时清理会话
- `detect_generation_error()` — 检测页面是否显示模型错误（额度用完、服务不可用等），返回 `str | None`
- `startup_health_check()` — 基类已通过 selector_config 实现通用版本，一般无需覆写

**文件上传**
- `before_file_send()` — 上传前记录上下文
- `upload_file()` — 上传文件逻辑

**回复检测**
- `try_handle_reply_preference_ui()` — 处理双回复选择 UI

**更多 JS 脚本**（基类已声明，可选覆写）
- `reply_complete.js` — 精确判断回复完成（否则用 `not generation_in_progress`）
- `user_count_advanced.js` — 高级发送成功检测，签名为 `(arg) => { const [selectors, previousCount] = arg; ... }`

## 5. 注册

在 `model_clients/__init__.py`：

```python
from .xxx import XxxClient
```

在 `run.py` 的 `MODEL_CLIENTS`：

```python
MODEL_CLIENTS = {
    "qwen": QwenClient,
    "deepseek": DeepSeekClient,
    "xxx": XxxClient,
}
```

使用：

```bash
python run.py --model xxx
```

## 6. 登录态导出（可选）

在 `scripts/export_state.py` 的 `EXPORT_MODELS` 中增加一个 `ExportSpec`：

```python
"xxx": ExportSpec(
    model="xxx",
    display_name="Xxx",
    url=XXX_URL,
    default_output=Path.cwd() / "config" / "storage_state_xxx.json",
    default_user_data_dir=Path.cwd() / "config" / "login_profile_xxx",
    login_message="请在浏览器中完成登录。",
    timeout_error_prefix="等待 Xxx 登录超时",
    origin_keyword="xxx",
    local_storage_label="xxx",
    check_login=check_xxx_login,
    is_logged_in=is_xxx_logged_in,
),
```

## 7. Adapter 方法速查

按基类 section 分组（详见 `browser_base.py`）：

| Section | 方法 | 必须覆写 | 说明 |
| --- | --- | --- | --- |
| 配置 | `selectors.yaml` | 是 | CSS 选择器配置（composer, send_button, reply_content 等） |
| 类属性 | JS 脚本名 | 是 | 声明使用的 JS 脚本文件（如 `LATEST_REPLY_SCRIPT = "latest_reply.js"`） |
| 抽象生命周期 | `default_url()` | 是 | 目标站点 URL |
| | `is_logged_in()` | 是 | 登录状态判断 |
| | `wait_until_ready()` | 是 | 等待页面可交互（使用 `self.selector_config.composer`） |
| 可选生命周期钩子 | `after_page_loaded()` | 否 | 额外初始化 |
| | `login_state_guidance()` | 建议 | 登录引导文案 |
| | `after_message_sent()` | 否 | 发送后钩子 |
| | `cleanup_session()` | 否 | 清理会话 |
| | `detect_generation_error()` | 建议 | 检测模型错误（额度/服务不可用），返回 `str \| None` |
| | `startup_health_check()` | 否 | 基类通过 selector_config 检查关键选择器 |
| 文本发送 | `before_text_send()` | 是 | 填文本、返回上下文计数 |
| | `send_current_message()` | 否 | 基类通用 Enter→按钮 发送 |
| | `wait_until_sendable()` | 否 | 基类通过 selector_config.send_button 等待 |
| 文件上传 | `before_file_send()` | 按需 | 上传前上下文 |
| | `upload_file()` | 按需 | 文件上传逻辑 |
| | `fill_prompt_after_upload()` | 否 | 基类已实现 fill/insert_text fallback |
| 回复检测 | `latest_reply_text()` | 否 | 基类通过 JS 脚本实现，自动传 selector_args |
| | `assistant_message_count()` | 否 | 基类通过 JS 脚本实现 |
| | `is_generation_in_progress()` | 否 | 基类通过 JS 脚本实现 |
| | `is_reply_complete()` | 否 | 基类取反 generation_in_progress |
| 内部工具 | `_composer_locator()` 等 | 否 | Playwright 底层封装，从 selector_config 取选择器 |
| | `_selector_args()` | 否 | 返回传给 JS 的选择器字典 |

## 8. 错误处理

基类已内置异常层次（`model_clients/exceptions.py`）：

| 异常 | 触发时机 | 处理 |
| --- | --- | --- |
| `LoginExpiredError` | `wait_for_reply()` 超时或 Playwright 异常时检测到登录过期 | 附带登录引导信息 |
| `GenerationFailureError` | `wait_for_reply()` 轮询中 `detect_generation_error()` 返回非空 | 截图后抛出，附错误文本 |
| `NetworkError` | Playwright 通信异常 | 附重试建议 |
| `SelectorNotFoundError` | `startup_health_check()` 检测到选择器缺失 | 通过 notice 通知用户 |

## 9. 常见坑

- 输入框是 `contenteditable`，`fill()` 无效 → fallback `click()` + `insert_text()`
- Enter 只换行不发送 → 覆写 `send_current_message()` 或设 `prefer_button=True`
- `is_generation_in_progress()` 一直 `True` → 导致等待超时，检查 JS 脚本
- `is_reply_complete()` 过早 `True` → 回复截断，需实现精确 `reply_complete.js`
- 文件消息无 prompt 时不能依赖输入框清空判断发送成功 → 传 `had_text=False`
- JS 脚本必须接收 `selectors` 参数，不能硬编码 CSS 选择器
- `user_count_advanced.js` 等需要额外参数的脚本，使用 `(arg) => { const [selectors, extra] = arg; }` 解构模式
- 不要打印 cookie、localStorage value 等敏感信息
- 网站改版时优先修改 `selectors.yaml`，不要改动 Python 代码中的选择器
