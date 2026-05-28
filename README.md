# CheapClaw

基于 Playwright 的[通义千问](https://chat.qwen.ai/)网页版终端客户端。通过自动化浏览器模拟用户交互，无需 API Key 即可在终端中使用千问。

## 快速开始

```bash
pip install -r requirements.txt   # 需要 playwright、rich 等依赖
playwright install chromium        # 安装 Playwright 浏览器

python main.py                     # 启动对话
```

首次启动会自动打开浏览器，在浏览器中登录千问后，登录状态会被持久化，后续自动复用。

## 项目结构

```
CheapClaw/
├── main.py                 # 入口：组装各组件并启动 UI
├── llm/
│   └── qwen.py             # QwenClient — Playwright 浏览器自动化
├── bot/
│   ├── memory.py           # 分层对话记忆系统（三级架构）
│   └── assembler.py        # Prompt 组装器
├── ui/
│   └── rich_cli.py         # 终端 UI（基于 rich 库）
├── utils/
│   ├── log.py              # 日志 + 截图工具
│   └── processor.py        # 文本清理（去行号、Markdown 转纯文本）
└── config/
    └── qwen_logged_in/     # 持久化浏览器用户数据（登录态）
```

## 架构

整个系统分四层：

### 1. 浏览器自动化层 (`llm/qwen.py`)

`QwenClient` 使用 Playwright 控制 Chromium 浏览器，通过 CSS 选择器定位页面元素来模拟交互：

- **登录机制**：启动时加载持久化的 Chrome 用户数据目录（`config/qwen_logged_in`），自动复用之前的登录 session
- **登录检测**：导航后检查 URL 是否被重定向到登录页 + 确认聊天输入框存在；若 session 过期则等待用户在浏览器中手动登录
- **消息发送**：`send_text()` 填入输入框 → 移除 `maxlength` 限制 → 点击发送 → 轮询等待回复完成
- **回复等待**：`wait_for_reply()` 轮询检测操作容器 `.message-hoc-container` 出现，同时自动关闭弹窗
- **文件上传**：`send_file()` 定位文件上传 input → 上传文件 → 等待解析 → 点击发送

### 2. 记忆层 (`bot/memory.py`)

三级分层记忆架构：

| 层级 | 组件 | 职责 | 容量 |
|------|------|------|------|
| Level 1 | `ConversationBuffer` | 最近对话完整保留 | ~3000 tokens（可配） |
| Level 2 | `MemoryCompressor` | 历史对话 LLM 摘要 | 最多 5 条摘要 |
| Level 3 | `KeyInfoStore` | 用户偏好/决策 key-value | 无限制 |

- 每次添加消息后自动检查 Level 1 是否超过阈值（80%）
- 超过时优先调用 LLM 将最早的消息对压缩为摘要（Level 2）
- 若 LLM 不可用或压缩后仍超限，丢弃最旧消息对兜底
- `get_context()` 按优先级组装：关键信息 → 历史摘要 → 最近对话
- 各级数据统一持久化到 `memory.json`

### 3. 组装层 (`bot/assembler.py`)

`Assembler` 将系统设定 + 记忆上下文 + 用户当前输入拼成一段文本，作为发给千问的 prompt。

### 4. UI 层 (`ui/rich_cli.py`)

`RichCLI` 基于 `rich` 库，提供语法高亮、面板等终端 UI 增强。

## CLI 命令

在对话中输入以下命令：

| 命令 | 功能 |
|------|------|
| `/clear` | 清屏 |
| `/compress` | 手动触发记忆压缩（将最旧的对话转为 LLM 摘要） |
| `/exit` | 退出 |

## 配置

### 系统提示词

在 `main.py` 中修改 `system_prompt` 参数来设定 AI 的角色行为。

### 记忆参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `buffer_max_tokens` | 3000 | 工作记忆 token 上限 |
| `max_summaries` | 5 | 最多保留的摘要条数 |

### 浏览器

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `headless` | False | 是否无头模式 |
| `timeout` | 120000ms | 操作超时 |
| `user_data_dir` | `./config/qwen_logged_in` | 持久化用户数据目录 |

## 日志与调试

- 日志文件：`debug/{YYYYMMDD}.log`
- 截图：`debug/screenshots/`，关键步骤自动截图（登录、发送、回复等待、关闭）

## 注意事项

- 本工具通过 UI 自动化操作千问网页版，依赖具体的 CSS 选择器；千问前端改版可能导致需要更新选择器
- 运行时会打开浏览器窗口（`headless=False`），不能纯后台运行
- 压缩记忆时会在千问网页上发送一条摘要 prompt（可见但不影响正常使用）

## 依赖

- `playwright` — 浏览器自动化
- `rich` — 终端 UI 增强
- `markdown` / `beautifulsoup4` — 文本处理