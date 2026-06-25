# Memory 记忆系统

CheapClaw 采用三层分层记忆架构，在有限的上下文预算内最大化信息保留。

## 三层架构

```
┌──────────────────────────────────────┐
│              Memory                  │
│          统一入口，对外 API            │
├──────────────────────────────────────┤
│                                      │
│  ┌────────────────────────────┐      │
│  │   Level 1: ConversationBuffer│      │
│  │   工作记忆 — 最近完整对话      │      │
│  └──────────┬─────────────────┘      │
│             │ 超出阈值时              │
│             ▼                        │
│  ┌────────────────────────────┐      │
│  │   Level 2: MemoryCompressor │      │
│  │   压缩记忆 — LLM 摘要        │      │
│  └────────────────────────────┘      │
│                                      │
│  ┌────────────────────────────┐      │
│  │   Level 3: KeyInfoStore    │      │
│  │   关键信息 — 持久化偏好      │      │
│  └────────────────────────────┘      │
│                                      │
└──────────────────────────────────────┘
```

### Level 1: ConversationBuffer（工作记忆）

保留最近的完整对话消息（含 token 估算和时间戳）。Token 估算不依赖外部 tokenizer：中文约 1.5 token/字，英文约 0.3 token/字符。当 token 总量超过阈值时自动触发压缩或裁剪。

### Level 2: MemoryCompressor（压缩记忆）

当工作记忆超出阈值时，将较早的消息通过 LLM 压缩为摘要。摘要列表按时间排序，超出上限时合并最早的两条。

压缩触发条件：buffer token 数达到阈值比例、消息数足够、有可用的 LLM 调用接口。

### Level 3: KeyInfoStore（关键信息）

持久化的 key-value 存储，用于跨 session 保留用户偏好、技术决策和项目约定。

## 上下文组装流程

`Memory.get_context(query)` 是核心 API，按预算分配依次选取：

1. **最近对话**：从最新消息开始向前取，不超过预算
2. **KeyInfo 检索**：通过 BM25 评分选取相关条目
3. **Summary 检索**：通过 BM25 评分选取相关摘要

对于 "继续"、"好的"、"ok" 等通用短查询，跳过 KeyInfo 和 Summary 检索，只保留最近对话，避免无关信息污染上下文。

## BM25 检索

CheapClaw 当前不支持 embedding，因此未采用向量检索或混合检索。项目通过 Playwright 复用网页模型的对话能力，但网页端没有可用的 embedding API；增加 embedding 能力意味着需要引入额外的 API 依赖（如付费 embedding 服务或本地模型），这与 CheapClaw"不依赖 API Key、不额外烧 token"的定位相悖。

BM25 在轻量级记忆检索场景下效果足够，配合 jieba 中文分词后对混合语言文档也有较好的召回。使用标准 BM25 算法对 key-info 和 summary 分别评分：

- **分词**：英文正则提取词项 + 中文 jieba 分词（精确模式）
- **相关性**：query 非空时过滤 score <= 0 的结果，并为每条记录增加微小位置权重防偏差
- **参数**：BM25 的 k1（词频饱和度）和 b（文档长度归一化）均可配置

## 自动压缩与裁剪

- **自动压缩**：工作记忆 token 数达到阈值时，保留最近 N 条消息，将更早的消息通过 LLM 压缩为摘要
- **自动裁剪**：压缩后 token 仍超阈值时，逐批弹出最旧消息
- **手动压缩**：用户通过 `/compress` 命令手动触发

## Session 管理

- **会话归档**：退出时将对话消息归档为 `memory-{session_id}.json`，可选地先压缩再归档
- **跨 session 持久化**：`Memory.save()` 写入 key-info、session 历史等跨 session 信息；`Memory.load()` 在下次运行时恢复

## 配置参考

所有参数位于 `config/default_config.json` 的 `memory` 段。核心概念性参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `context_budget` | 上下文总 token 预算 | 4000 |
| `buffer_max_tokens` | 工作记忆 token 上限 | 3000 |
| `max_summaries` | 摘要列表最大条数 | 20 |
| `auto_compress_threshold` | 触发自动压缩的 token 占比 | 0.8 |

完整配置项（含 BM25 参数、检索预算分配、短查询列表等）见配置文件本身。所有参数均有 Pydantic 校验和默认值。
