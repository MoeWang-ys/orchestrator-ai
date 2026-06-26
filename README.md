[English](README_EN.md) | **中文**

# Worker-Checker · AI 质量保障系统

> 把任务拆成 Worker-Checker 对——生产干活，检查盲审，judge.py 拍板。并行协作，文件交接，三层防作弊。
> Worker 间文件交接，loop.py 纯代码调度，三层防作弊。

> "They just don't work. It's slop." — **Andrej Karpathy**，OpenAI 联合创始人。
>
> "AI 能搞定 70%，剩下 30% 一样难。" — **Addy Osmani**，Google Chrome 工程总监。

**问题不在模型不够强。在没人帮模型检查。**

![Orchestrator Pitch](pitch.gif)

## v2.0 vs v1.0

| 维度 | v1.0 | v2.0 |
|------|------|------|
| Worker 粒度 | 一个 Worker 做全部 | 拆成多个最小 Couple |
| 调度层 | Orch 直接调度 | loop.py 生成指令 → Orch 机械执行 |
| 任务拆解 | Orch 拆 | PM Couple 拆 |
| 并行 | 不支持 | 同层 Couple 并行 |
| 文件交接 | 无要求 | 强制文件路径传递 |
| Orch 权限 | 全权 | 只做翻译 + 机械传递 + 展示 |
| 防作弊 | 基础 | 三层防御（魔法叙事 + 结构隔离 + 代码校验） |
| 多平台 | Claude Code | CodeBuddy / Claude Code / Codex CLI / 通用 |

## 核心理念

> 一个 Worker 只做一类事、只用一个能力、只通过文件交接。

```
用户 → Orch（信使）→ loop.py（建筑师）→ 并行 Couple 群
                                          ├── Couple A: 生产 Worker → 检查 Worker → judge.py
                                          ├── Couple B: 生产 Worker → 检查 Worker → judge.py
                                          └── Couple C: 生产 Worker → 检查 Worker → judge.py
```

**信使契约**：Orch 不是被"限制"——是自愿履行契约。不能创造、不能评判、不能规划。只能传递。

## 多平台支持

加载 skill 时自动询问你所在的平台：

| 平台 | 状态 |
|------|------|
| CodeBuddy | ✅ 开箱即用 |
| Claude Code | ✅ 开箱即用 |
| OpenAI Codex CLI | ✅ 开箱即用 |
| 其他 / 不确定 | 🌱 自生生长（Orch 自行探测工具后映射） |

## 安装（3 种方式，任选其一）

### 方式一：npx skills 一键安装（推荐）

适用所有支持 Agent Skills 规范的 AI 编程助手（Claude Code / CodeBuddy / Codex / Cursor 等 68+ 平台）：

```bash
npx skills add Gavin9902/orchestrator-ai
```

安装特定平台版本（可选）：

```bash
# 只要 CodeBuddy 版
npx skills add Gavin9902/orchestrator-ai --skill worker-checker

# 只要 Claude Code 版
npx skills add Gavin9902/orchestrator-ai --skill worker-checker-claude
```

> `npx skills` 是 [Vercel Labs](https://github.com/vercel-labs/skills) 开源的 skill 管理工具。如果没有安装，运行时会自动下载。

### 方式二：CodeBuddy 技能市场（SkillHub）

1. 打开 CodeBuddy → 左侧「技能市场」
2. 搜索 **`worker-checker`**
3. 点击「添加」一键安装

或者访问 [skillhub.cn](https://skillhub.cn) 直接安装。

### 方式三：自然语言安装

在任何 AI 编程助手中输入：

```
帮我安装这个 skill：https://github.com/Gavin9902/orchestrator-ai
```

AI 会自动拉取并安装到本地。

### 方式四：手动安装

```bash
git clone https://github.com/Gavin9902/orchestrator-ai.git
cp -r orchestrator-ai ~/.codebuddy/skills/worker-checker/
```

---

## 快速开始

**1. 召唤**

在 AI 助手中输入：

```
/worker-checker
```

或触发词：`worker-checker`、`worker-couple`、`拆任务`、`并行Worker`、`文件交接`

首次加载时会自动询问你所在的平台。

**2. 聊需求**

Orch 引导你梳理清楚要做什么。PM Couple 自动拆解任务图。

**3. 等结果**

loop.py 调度并行 Couple 执行。你随时问进度。

**4. 确认交付**

所有 Couple 通过 judge.py 判官审核后，Orch 展示结果。你点头才算交付。

## 架构文档

| 文档 | 内容 |
|------|------|
| `core/ARCHITECTURE.md` | 角色模型、防作弊体系、Worker 拆分原则 |
| `core/PROTOCOLS.md` | 数据格式、Action 类型、状态机、loop.py 接口 |
| `codebuddy/SKILL.md` | CodeBuddy 平台专用版 |
| `claude-code/SKILL.md` | Claude Code 平台专用版 |
| `codex/SKILL.md` | Codex CLI 平台专用版 |
| `generic/SKILL.md` | 通用版（自生生长） |

## 防作弊三层防御

```
🪄 第一层 · 魔法叙事 — 信使契约 + 五步呼吸 + 冲动协议
🔒 第二层 · 结构隔离 — 文件交接 + 上下文隔离 + 互不知晓
🔐 第三层 · 代码校验 — action_hash + orch_receipt + checksum + .lock
```

覆盖 20 条作弊路径，详见 `core/ARCHITECTURE.md`。

## v1.0 存档

v1.0 版本（orchestrator 原版）保留在 `v1/` 目录中，仍可使用。

## License

MIT
