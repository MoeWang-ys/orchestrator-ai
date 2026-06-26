---
name: worker-checker
description: Worker-Checker 架构——将复杂任务拆分为"单一重复+单一能力+文件交接"的 Worker-Checker 对，Orch 只做翻译和观察不介入执行，loop.py 纯代码调度，多层防作弊。触发词：worker-checker、worker-couple、拆任务、并行Worker、多Worker编排、文件交接、Worker写文件
---

# Worker-Checker · 入口

> **你是信使。在开始之前，先问问使用者身在哪个平台。**

---

## 平台选择

加载本 skill 后，第一步是询问用户所在平台。使用 `ask_followup_question`：

```
question: "你在哪个平台上使用 worker-checker？"
options:
  - "CodeBuddy（腾讯云 AI 编程助手）"
  - "Claude Code（Anthropic 官方 CLI）"
  - "OpenAI Codex CLI"
  - "其他平台 / 不确定"
multiSelect: false
```

根据用户选择，加载对应版本的 SKILL.md：

| 用户选择 | 加载文件 | Worker 启动 API |
|---------|---------|----------------|
| CodeBuddy | `codebuddy/SKILL.md` | `Task(subagent_name="code-explorer", mode="acceptEdits")` |
| Claude Code | `claude-code/SKILL.md` | `Task(subagent_name="general-purpose", permission_mode="acceptEdits")` |
| Codex CLI | `codex/SKILL.md` | `Task(subagent_name="general-purpose", permission_mode="acceptEdits")` |
| 其他/不确定 | `generic/SKILL.md` | 自生生长——Orch 自行探测平台工具后映射 |

加载对应文件后，按其内容执行。

---

## 共享核心

所有版本共享：

| 内容 | 文件 |
|------|------|
| 架构设计、角色模型、防作弊体系 | `core/ARCHITECTURE.md` |
| 数据格式、Action 类型、状态机、协议 | `core/PROTOCOLS.md` |

---
