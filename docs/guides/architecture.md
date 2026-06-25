# CheapClaw 架构概览

## 项目分层

项目按职责分为 4 个核心层：

```
agent_core/        ← 编排层：Agent 调度、Memory 记忆、ToolOrchestrator 工具编排
model_clients/     ← 模型接入层：网页模型客户端、浏览器自动化、站点适配器
ui/                ← 界面层：终端交互 UI
utils/             ← 工具层：日志、文本处理
```

每个网页模型（deepseek/qwen）在 `model_clients/` 下有独立子包，包含客户端实现、CSS 选择器 YAML 和 JS 页面脚本。配置系统基于 Pydantic Frozen 模型，从 `config/default_config.json` 加载，分为 Agent/Browser/Memory 三个配置段。

## 启动流程

`run.py` 作为入口，按依赖顺序组装系统：

```
解析 CLI 参数 → 加载配置 → 创建 Client → 创建 Memory → 创建 Workspace → 创建 Agent → 启动 CLI
```

## 一轮对话的数据流

用户输入后，Agent.run_turn() 按以下流程处理：

1. 检查是否有待确认的命令（如 file replace / 非白名单命令），有则直接进入确认流程
2. 若启用了工具编排，进入 **ToolOrchestrator.run_turn()**：
   - **Router** 先判断用户意图：需要终端工具还是纯对话
   - Router 判定"对话" → 回退到 Legacy 路径
   - Router 判定"工具" → 进入 Planner-Execute 循环（最多 N 步）
3. 若未启用工具编排（或无 workspace），直接走 Legacy 路径：
   - Memory 检索上下文 → Prompt 组装 → 超长自动文件上传 → 发送到网页模型
4. 最终更新 Memory（添加消息 → 可能触发压缩 → 持久化）

## 模块职责与通信关系

```
┌─────────────────────────────────────────────────────────┐
│                        run.py                           │
│  组装依赖图，启动 CLI                                      │
└────────┬──────────┬──────────┬──────────┬──────────────┘
         │          │          │          │
    ┌────▼──┐  ┌───▼───┐  ┌──▼─────┐  ┌▼─────────┐
    │Agent  │  │Memory │  │Workspace│  │XxxClient │
    │编排层  │  │记忆系统│  │路径沙箱 │  │网页模型   │
    └───┬───┘  └───┬───┘  └───┬────┘  └────┬─────┘
        │          │          │             │
   ┌────▼────┐     │     ┌────▼─────┐       │
   │Transport│     │     │ToolOrch  │       │
   │文件传输  │     │     │工具编排   │       │
   └────┬────┘     │     └────┬─────┘       │
        │          │          │             │
        └──────────┴──────────┴─────────────┘
                       │
                  Protocol:
                  AgentClient
```

### Agent（编排层）

集中管理一轮对话的完整生命周期。持有 Memory、PromptTransport、ToolOrchestrator 和 AgentClient 的引用，在它们之间传递数据和事件。不包含具体业务逻辑，只做调度。

### Memory（记忆系统）

三层分层记忆（工作记忆 → 压缩摘要 → 关键信息持久化）。统一入口 `Memory.get_context(query)` 根据用户输入从三层中智能检索并组装上下文。详见 [memory.md](memory.md)。

### ToolOrchestrator（工具编排）

终端工具调用的编排器，执行 Router → Planner → Execute → Confirm 循环。详见 [tools.md](tools.md)。

### PromptTransport（文件传输）

透明地将超长 prompt 从直接文本发送切换为文件上传。由于网页模型的文本输入框有长度限制，超过阈值的 prompt 会先写入临时文件再调用 `client.send_file()` 上传。

### 模型客户端层

- **AgentClient Protocol**（`model_clients/protocols.py`）：定义客户端必须实现的接口（`start/send_text/send_file/close/consume_notices`），Agent 只依赖此 Protocol。
- **BrowserModelClient**（`model_clients/browser_base.py`）：基于 Playwright 的通用实现，封装浏览器生命周期和消息收发流程。子类只需注入对应的 Adapter。
- **BrowserFrontendAdapter**：抽象基类，定义站点差异接口。每个站点通过子类 + `selectors.yaml` 实现差异化逻辑，网站改版时只需修改 YAML。
- **异常层次**（`model_clients/exceptions.py`）：`CheapClawError → BrowserError → LoginExpiredError / GenerationFailureError / NetworkError / SelectorNotFoundError`。

## 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 客户端接口 | Python Protocol | 不强制继承，支持 duck typing，便于测试 mock |
| 选择器配置 | YAML 外部化 | 网站改版时无需改代码，用户可直接编辑 |
| 命令安全 | 黑白名单 + `shell=False` | 既保证安全性，又允许常见无害命令 |
| 记忆检索 | BM25 + jieba 分词 | 轻量、无外部依赖，中文分词效果好 |
| 错误处理 | Python 异常层次 | 符合 Python 惯用法，可精确捕获和分层处理 |
| 命令执行 | `subprocess.run(shell=False)` | 杜绝 shell 注入 |
