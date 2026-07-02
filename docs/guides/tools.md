# 终端工具系统

CheapClaw 支持通过网页模型驱动终端命令来读取工作区文件，以及进行受控的文件修改。工具系统由三层组件协作完成。

## 架构分层

```
ToolOrchestrator          ← 编排层：路由 → 规划 → 执行 → 确认
      │
      ▼
TerminalCommandPolicy     ← 策略层：命令解析、黑白名单校验、安全约束
      │
      ▼
ControlledTerminalRunner  ← 执行层：cwd 维护、子进程运行、checkpoint、file replace
```

## TerminalCommandPolicy（策略层）

实现于 `cheapclaw/agent_core/tool_commands.py`。

### 安全模型

| 安全机制 | 实现 |
|---|---|
| 黑名单 | 被禁止的命令名（默认 `rm`） |
| 白名单 | 无需用户确认即可自动执行的命令名 |
| Shell 操作符禁止 | `\|\|`, `&`, `&&`, `>`, `>>`, `<`, `<<`, `<<<` |
| 禁止变量展开 | `$` 符号 |
| 禁止命令替换 | `` ` `` 符号 |
| 禁止环境变量赋值 | `VAR=value` 模式 |
| 禁止通配符路径 | file builtin 不允许 `*`, `?`, `[`, `]` |

### 安全管道

管道 `|` 在满足以下全部条件时被允许：

1. 管道中的每个命令都在白名单内
2. 管道末端消费者必须是只读命令：`head`, `tail`, `grep`, `wc`, `sort`, `uniq`, `cut`, `tr`, `sed`
3. 不得与重定向、后台执行、命令连接等其他 shell 操作符混用
4. 所有管道命令都需要用户确认后才能执行

管道通过 Python 级 `subprocess.Popen` 链式连接执行，不使用 `shell=True`，避免 shell 注入风险。

### `shell=False` 安全保障

所有终端命令通过 `subprocess.run(argv, shell=False)` 执行。非管道命令中 `;`、`&` 等字符在此模式下只是普通参数，不会被 shell 解释。安全管道单独处理 `|`，通过 Popen 链式执行。

### 内置伪命令

不通过 subprocess 执行，由 ControlledTerminalRunner 直接处理：

| 命令 | 说明 |
|---|---|
| `file replace <path>` | 文件写入（需 diff 预览和用户确认） |
| `checkpoint list` | 列出所有 checkpoint |
| `checkpoint restore <id>` | 恢复到指定 checkpoint（需用户确认） |

### 确认策略

- 白名单内的命令 → 自动执行
- 管道命令（即使全部命令都在白名单内）→ 总是需要确认
- `file` builtin、`checkpoint restore` → 总是需要确认
- 其他命令 → 需要确认

用户通过回复确认关键词（默认 `y`, `yes`, `确认`, `执行`, `是`）执行，回复 `no` 取消。

## ControlledTerminalRunner（执行层）

实现于 `cheapclaw/agent_core/tool_commands.py`。

### cwd 管理

维护会话级工作目录 `cwd`，初始为 workspace 根目录。`cd` 命令更新 `cwd`，所有后续命令在此目录执行。

### 子进程执行

通过 `subprocess.run(argv, shell=False, cwd=cwd, timeout=15)` 执行，输出自动截断到字符上限。

### File Replace 工作流

两步流程：规划器输出 `file replace` 命令 → LLM 生成文件内容 → 计算 diff 并展示预览 → 用户确认 → 原子写入。

对于“请修改”“按刚才方案改”等连续请求，工具系统会尽量沿用上一轮已明确的目标和方案；修改前仍会重新读取目标文件，并在写入前展示 diff 供确认。

**原子写入**：内容先写临时文件，`flush()` + `fsync()` 后再 `rename()` 到目标路径，保证断电安全。

### Checkpoint 系统

每次 file replace 执行前自动创建 checkpoint，保存文件原始内容副本。自动清理保留最近 N 个 checkpoint。恢复时从备份还原或删除新建文件。

**注意**：checkpoint 是会话级临时保护，仅在当前 session 内有效。正常退出时 checkpoint 目录会被自动清理（`cleanup_checkpoints()`）。这意味着跨 session 无法恢复之前写入的文件，checkpoint 的定位是帮助"本次对话中撤销修改"，而非持久化版本管理。

## ToolOrchestrator（编排层）

实现于 `cheapclaw/agent_core/tool_orchestrator.py`。

### 执行流程

```
用户输入
  │
  ▼
Router: 判断是否需要终端工具（必要时用工作区信号辅助判断）
  │
  ├─ "对话" → 返回 None，Agent 回退到 Legacy 对话路径
  │
  └─ "工具" → 进入 Planner Loop（最多 N 步）:
      │
      ├─ LLM 规划 → 生成 command 或 final
      ├─ 黑名单检查
      ├─ file replace → 生成内容 → diff 预览 → 待确认
      ├─ 需确认命令 → 中断循环，等用户回复
      └─ 自动执行 → 收集 observation → 继续循环
      │
      ▼
  达到步数上限 → 基于所有 observations 强制生成最终回答
```

当命令需要确认时，编排器中断循环，保存当前状态（命令、已收集的 observations、用户输入等）。下一轮对话检测到待确认命令后，根据用户回复执行或取消。

## 配置参考

所有参数位于 `config/default_config.json` 的 `agent.tools` 段。核心概念性参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `command_blacklist` | 被禁止的命令 | `["rm"]` |
| `command_whitelist` | 自动执行的命令 | `["ls", "cd", "cat", "find", "grep", "head", "tail", "wc"]` |
| `observation_text_limit` | stdout/stderr 截断字符数 | 8000 |
| `subprocess_timeout_seconds` | 子进程超时秒数 | 15 |

Agent 级参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `agent.max_text_chars` | 触发文件传输的文本长度阈值 | 10000 |
| `agent.tool_orchestration_enabled` | 是否启用工具编排 | true |
| `agent.max_tool_steps` | 单轮最大工具调用步数 | 5 |

如果需要调整哪些表达会被视为工作区相关请求，可参考 `workspace_*` 配置项；普通用户通常无需修改。完整配置项（含 checkpoint 保留数、确认关键词、diff 预览截断等）见配置文件本身。所有参数均有 Pydantic 校验和默认值。
