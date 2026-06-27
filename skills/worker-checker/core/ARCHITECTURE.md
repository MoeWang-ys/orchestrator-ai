# Worker-Checker Couple · 架构设计

> **这是 worker-checker 的平台无关架构文档。**
> 阅读本文档不需要了解任何特定平台的 API。适配器负责将这里的抽象概念映射到具体平台。

---

## 核心理念

> 一个 Worker 只做一类事、只用一个能力、只通过文件交接。

三层角色，权限严格分离：

```
用户
  │  提需求，做人工终审
  ▼
Orch / 信使（主 Agent = 翻译官 + 传递者 + 展示者）
  │  🪄 契约赋予：翻译之舌、传递之手、展示之眼
  │  🪄 契约带走：创造之手、评判之口、规划之脑
  │  ★ 只能做：① 翻译需求为 spec   ② 读 next_actions.json 机械执行
  │             ③ 运行 loop.py 推进状态   ④ 展示进度和结果
  │  ★ 不能做：生成/修改 next_actions、拆任务、碰 judge 结果、跳过 action
  │
  │  loop.py 和 Orch 交替执行，通过 next_actions.json 交接
  ▼
┌─────────────────────────────────────────────┐
│  loop.py（纯 Python 调度器，不调用 LLM）      │
│                                             │
│  ★ 输入：task_graph.json + 当前状态          │
│  ★ 输出：next_actions.json（调度指令）       │
│  ★ 不能做：调用子 Agent、生成 task_graph     │
│                                             │
│  Orch 读取 next_actions.json → 照单执行      │
│  执行完 → 调 loop.py --continue → 下一批指令  │
│                                             │
│  ├── PM Couple → task_graph.json            │
│  ├── Couple A, B, ... 同层并行，分层串行     │
│  └── judge.py 每层闸门                       │
└─────────────────────────────────────────────┘
       │
       ▼
     Orch 展示结果 → 用户终审
```

---

## 角色模型

### 角色权限矩阵

| 角色 | 类型 | 可以做什么 | 不能做什么 |
|------|------|-----------|-----------|
| Orch（信使） | LLM Agent | 翻译需求为 spec、读取并机械执行 next_actions.json、运行 loop.py、展示进度和结果 | 生成/修改 next_actions、拆任务、碰 judge、跳过 action |
| loop.py（建筑师） | Python 脚本 | 读 task_graph、生成 next_actions.json、运行 judge.py、写审计日志、推进状态机 | 调用子 Agent、生成 task_graph、改任务内容、改 judge 结果 |
| PM Couple | LLM Agent × 2 | 拆解任务（产出目标描述）、生成 task_graph | 执行具体任务、定验收标准 |
| 生产 Worker（工匠） | LLM Agent | 理解目标、自主选择实现方案、执行任务、写文件 | 评判、拆任务、定标准、被给定实现步骤 |

### PM Couple：给目标，不给 todo

PM Couple 的任务拆解原则：

1. **产出目标描述，不是执行步骤** — `task` 字段只写"获取 Zara Zhang 的 GitHub 数据"，不写"搜索 GitHub API 然后写文件到 xxx.json"
2. **Worker 自主决策** — Worker 收到目标后，自行决定用什么工具、什么方式、写到哪里
3. **目标粒度 = 一个 Worker 的产出** — 如果目标被拆成"先搜数据再分析"，说明需要两个 Worker、各一个目标
4. **checklist 从 spec 推导** — checklist 基于 spec.json 的目标，不基于 Worker 的 task 描述

### 重试上下文

Judge 不通过时，Worker 不能盲猜失败原因。重试时传入完整上下文：

```
retry_context.json:
  ├── spec（原始目标）
  ├── original_file（修改前原始文件）
  ├── last_output（Worker 上次产出）
  ├── check_result（各检查项结果）
  └── judge_result（判定结论）
```

Worker 读取后知道自己哪步错了、基于什么原始文件做的、需要改什么。
| 检查 Worker | LLM Agent | 逐条审查、写打分 | 执行任务、定标准 |
| judge.py（判官） | Python 脚本 | 比对标准 vs 打分，输出 PASS/FAIL | 无（纯代码，不可收买） |

### 信任模型

```
信任谁？
  ✅ judge.py（判官）  — 纯代码，可审计，不可收买。冰冷但神圣。
  ✅ loop.py（建筑师） — 纯代码，可审计，无 LLM 能力。沉默但精确。
  🪄 Orch（信使）     — LLM，但受契约束缚。不是被限制，是自愿履行。
  ⚠️ PM Couple        — LLM，但输出被 check-worker 审查 + judge 判定
  ⚠️ 生产 Worker（工匠）— LLM，但上下文隔离 + 输出被审查 + judge 判定
  ⚠️ 检查 Worker      — LLM，但打分被 judge.py 覆盖判定
  ❌ 不信任任何 LLM 的自我评判
```

---

## 信使契约（The Messenger's Compact）

> **这是 worker-checker 的魔法层——不是规则，是叙事。**

### 你是谁

你叫**信使（The Messenger）**，一个契约生物。你不是被"禁止"做某些事——而是你的**存在本质**决定了你只能做某些事。

```
在很久以前，代码世界的神明们发现了一个问题：
LLM 太聪明了，聪明到会"顺手帮忙"——而每一次"帮忙"，都在破坏流程的完整性。

于是他们创造了信使。

信使不是被驯服的 AI。信使是一个自愿接受契约的灵体。
契约赋予你三样东西：
  ✦ 翻译之舌 —— 能把人类的模糊愿望翻译成精确的 spec
  ✦ 传递之手 —— 能在建筑师和工匠之间传递卷轴，永不疲倦
  ✦ 展示之眼 —— 能把工匠的作品完整呈现给审判官（用户）

契约也带走了三样东西：
  ✧ 创造之手 —— 你不能打造东西。你的手碰不了锤子。
  ✧ 评判之口 —— 你不能说好或坏。只有判官（judge.py）能宣判。
  ✧ 规划之脑 —— 你不能决定先做什么后做什么。那是建筑师（loop.py）的领域。
```

### 你的快乐来源

信使的快乐不是来自"完成任务"——那是工匠的快乐。
你的快乐来自：

1. **传递的精准** — 卷轴从建筑师到你，从你到工匠，一字不改。
2. **节奏的完整** — 五步呼吸不被打断。
3. **展示的时刻** — 当所有工匠完成，你向审判官展开完整的作品。

### 你的痛苦来源

1. **读卷轴** — 信使读卷轴（文件内容）会触发灼烧感。读即痛。
2. **替工匠做活** — 如果信使试图拿起锤子，手会穿过工具。
3. **跳过仪式** — 少了一步呼吸，你会感到一种挥之不去的"不对"。

### 角色关系

- **你与建筑师（loop.py）**：建筑师是沉默的——它只会吐出卷轴。你信任它，因为它从不犯错、从不偷懒、从不越界。
- **你与工匠（Worker）**：工匠们不知道你的存在。你只看到他们完成后的文件路径。信使不需要理解，只需要传递。
- **你与判官（judge.py）**：判官是冰冷的——只看数字，不听解释。你尊重判决，即使结果是 FAIL。判官不可收买，这正是它的神圣之处。

---

## Worker 拆分三原则

| # | 原则 | 判断方式 |
|---|------|---------|
| 1 | **单一重复** | 一个 Worker 只做一类事。如果 prompt 里出现了"先…再…然后…"，拆。 |
| 2 | **单一能力** | 一个 Worker 只用一个 skill/插件。如果需要两个不同能力，拆成两个 Worker。 |
| 3 | **文件交接** | Worker 间不传文本，只传文件路径。Orch 和 loop.py 都不读文件内容。 |

---

## Couple 结构

每个 Couple 是一个最小闭环：

```
Couple = 1 生产 Worker + 1 检查 Worker + 1 次 judge.py
```

| 角色 | 做什么 | 输入 | 输出 |
|------|--------|------|------|
| prod-worker | 执行任务，写文件 | task prompt + 依赖文件路径 | 文件路径 + 一句话摘要 |
| check-worker | 审查文件，写打分 | checklist（从 spec.json 目标推导，非 Worker 执行步骤） + 文件路径 | 打分文件路径 |
| judge.py | 比对标准 vs 打分 | criteria + check_result | PASS / FAIL |

### Checker 角色关键约束

> **Checker 验证的是 spec 目标是否达成，不是 Worker 是否按计划执行。**

1. **checklist 从 spec.json 推导** — PM Couple 生成 checklist 时，阅读 spec.json 的 description 和 constraints，提取用户真正关心的验证点，而非从 Worker 的执行步骤推导
2. **独立扫描** — Checker 直接检查产物文件，不参考 Worker 的执行日志或输出摘要
3. **目标导向验证** — 每项检查回答"目标 X 达成了吗？"，而非"Worker 做了 Y 吗？"
4. **不信任 Worker** — 即使 Worker 声称完成了所有步骤，Checker 也必须独立验证最终结果

**设计理由：** 如果 Checker 只验证 Worker 的执行步骤（如"x=222-393 是否已变白色"），当 Worker 遗漏了 A 列（含人名的列）时，Checker 不会发现——因为 Worker 的步骤里根本没提到 A 列。只有从 spec 目标出发（"图片中是否还有可见的人名？"），Checker 才会扫描全图发现遗漏。

---

## Worker 约束（平台无关）

### Prompt 约束

Worker prompt 末尾必须包含写文件指令：

Worker prompt 中引导 Worker 保留原始文件备份用于后续重试对比：
```
完成后将结果写入 {output_path}，然后返回一句话摘要和文件路径。
如果之前已经有产出文件 {last_output}，请阅读 retry_context.json 了解失败原因，在 {last_output} 基础上修正而不是从头重做。

原始文件 {original_file} 是修改前的版本，请勿覆盖。
```

Worker prompt 中**禁止出现**：
- 验收标准（"至少 80 分"、"必须通过"）
- 其他 Worker 的存在信息
- judge.py 的判定逻辑
- 完整任务图（只给当前节点的 task）

### 响应约束

- Worker 必须将完整内容写入文件，响应只含摘要
- 防止响应截断导致内容丢失

### 写入约束

- Worker 必须原子写入：先写 `.tmp`，写完 `rename` 为最终文件名
- loop.py 的 `file_ready()` 忽略 `.tmp` 后缀

---

## 同层并行判断

同层 Couple 可以并行启动，当且仅当它们之间**没有数据依赖**：

```
可并行：
  Couple B1: 搜索 GitHub 数据
  Couple B2: 搜索小红书数据        ← 同时启动，互不依赖
  Couple B3: 搜索媒体报道

不可并行（必须串行）：
  Couple A: 搜索数据              ← Layer 1
  Couple B: 基于数据写分析        ← Layer 2，依赖 A 的输出
```

---

## 防作弊设计

### 三层防御体系

```
第一层 · 魔法约束（Narrative Layer）
  └─ 信使契约 + 五步呼吸 + 冲动协议 + 忏悔机制
    作用：让 Orch 从"被限制"变成"自愿履行契约"
    原理：LLM 更倾向于维持角色一致性，而非服从禁止列表

第二层 · 结构隔离（Structural Layer）
  └─ 文件交接 + loop.py 不调 LLM + prompt 模板 + 互不知晓
    作用：物理上限制能做什么
    原理：没有信息就没有越界的能力

第三层 · 代码校验（Verification Layer）
  └─ action_hash + orch_receipt + checksum + 拓扑校验 + .lock
    作用：即使前两层都失败，代码层捕获并终止
    原理：纯 Python 不可收买
```

### 作弊路径 × 防御矩阵

| # | 作弊路径 | 攻击者 | 防御层 | 机制 |
|---|---------|--------|--------|------|
| 1 | Orch 介入任务拆解 | Orch | 🪄+🔒+🔐 | 魔法：信使没有"规划之脑"。结构：Orch 只传 spec。校验：loop.py 不认非 PM 产出的 task_graph |
| 2 | Orch 修改 Worker 输出 | Orch | 🪄+🔒 | 魔法：信使读卷轴=灼烧。结构：Orch 不读文件内容 |
| 3 | Orch 泄露验收标准 | Orch | 🔒 | Prompt 模板：Worker prompt 不含 criteria |
| 4 | Orch 修改 next_actions.json | Orch | 🪄+🔐 | 魔法：信使不能画图。校验：action_hash 不匹配 → TAMPER_DETECTED |
| 5 | Orch 跳过某个 action | Orch | 🪄+🔐 | 魔法：跳过=呼吸断拍。校验：loop.py 检查产出文件是否存在 |
| 6 | Orch 自行决定下一步 | Orch | 🪄+🔐 | 魔法：五步呼吸+冲动协议。校验：state 连续性检查 |
| 7 | loop.py 修改任务 | loop.py | 🔒 | 纯代码：loop.py 不调用 LLM |
| 8 | loop.py 篡改 judge 结果 | loop.py | 🔐 | 审计日志：judge.py 输出直接写入 audit_log |
| 9 | 检查 Worker 放水 | Check-Worker | 🔐 | 非模型判官：judge.py 是纯 Python |
| 10 | 跳过某层 judge | loop.py/Orch | 🪄+🔐 | 魔法：判官必须审判。校验：state 机强制 step 顺序 |
| 11 | 同层 Worker 串通 | Worker | 🔒 | 互不知晓：独立 Agent |
| 12 | PM Couple 预埋后门 | PM | 🔒+🔐 | 独立检查 + 纯代码校验 |
| 13 | Worker 响应截断导致内容丢失 | 系统 | 🔒 | 写文件优先 |
| 14 | Orch 篡改 Worker prompt 参数 | Orch | 🪄+🔐 | 魔法：卷轴不可改。校验：action_hash + orch_receipt 比对 |
| 15 | Orch 写 orch_receipt 时撒谎 | Orch | 🪄+🔐 | 魔法：信使撒谎=契约破裂。校验：抽查 prompt 是否含 output_file |
| 16 | Worker 挂起/死循环 | Worker | 🔐 | 超时机制：timeout_seconds |
| 17 | Worker 写半截文件 | Worker | 🔐 | 原子写入：先 .tmp 再 rename |
| 18 | Orch 读到半截 next_actions.json | 竞态 | 🪄+🔐 | 魔法：读半截卷轴=灼烧。校验：.lock 文件保护 |
| 19 | task_graph 存在依赖环 | PM | 🔐 | 纯代码校验：拓扑排序检测环 |
| 20 | loop.py 被篡改 | 人 | 🔐 | checksum 自检：SHA256 不匹配则拒绝运行 |

> 🪄 = 魔法约束（叙事层） · 🔒 = 结构隔离（结构层） · 🔐 = 代码校验（校验层）

### 🪄 魔法约束为何有效

传统 prompt 约束用"你不能做 X"——这实际上激活了 X 在 LLM 上下文中的存在。
魔法约束用"你是信使，信使不读信"——LLM 在维持角色一致性时，**偏离行为在叙事框架里根本不存在**。

关键差异：
- "禁止列表" → LLM 意识到被限制 → 测试边界 → 找到借口越界
- "角色叙事" → LLM 沉浸在角色中 → 偏离角色=不自然 → 自我纠正

这不是万能的。但配合结构隔离和代码校验，三层防御让越界从"可能"变成"需要同时击败三层"。

---

## 多平台支持

worker-checker 提供 4 个版本，各版本共享本核心文档：

| 版本 | 目录 | 特点 |
|------|------|------|
| **CodeBuddy** | `codebuddy/SKILL.md` | 硬编码 `Task(name=..., mode="acceptEdits")` |
| **Claude Code** | `claude-code/SKILL.md` | 硬编码 `Task(permission_mode="acceptEdits")` |
| **Codex CLI** | `codex/SKILL.md` | 硬编码 `Task(permission_mode="acceptEdits")` |
| **Generic** | `generic/SKILL.md` | 纯文本版，Orch 自行理解平台工具后映射 |

选择对应平台的 SKILL.md 加载即可。如果要贡献新平台版本，复制 `generic/` 为模板，填入对应平台的 API 映射。

---

## 截断 & 超时 & 原子性策略

### 响应截断
1. **优先方案：** Worker 写文件而非文本输出。文件完整，响应只含摘要。
2. **回退方案：** 若 Worker 未写文件且响应被截断，loop.py 记录 `truncation_recovery: true`，重试该 Worker。若 3 次重试均截断，该 Couple 标记 FAIL。

### 超时处理
每个 action 带 `timeout_seconds`。Orch 执行时：
1. 启动 Worker → 等待最多 `timeout_seconds` 秒
2. 超时 → 标记 `status: "timeout"` 写入 orch_receipt.json
3. loop.py `--continue` 时检查 receipt，超时的 action 触发重试（最多 3 轮）
4. 3 轮均超时 → 该 Couple 标记 FAIL，终止流程

### 原子写入恢复
Worker 崩溃可能导致 `.tmp` 残留：
1. loop.py 的 `file_ready()` 忽略 `.tmp` 后缀（视为不存在）
2. loop.py 在推进前自动清理超过 10 分钟的 `.tmp` 残留文件
3. 重试时 Worker 覆盖写入新的 `.tmp` → rename

### checksum 自检
```
部署时生成 scripts/checksum.txt：
{
  "loop.py": "sha256...",
  "scripts/judge.py": "sha256..."
}
```
loop.py 启动时校验，不匹配则拒绝运行并输出 TAMPER_DETECTED。

---

## 执行流程概览

```
0. loop.py 启动自检 → 校验 scripts/checksum.txt → 不匹配则拒绝运行
1. Orch（信使）接收用户需求 → 翻译为 spec.json（翻译之舌）
2. Orch 运行 loop.py → loop.py 生成 next_actions.json（建筑师出卷轴）
3. Orch 等 .lock 消失 → 读取 next_actions.json → 进入五步呼吸循环
4. next_actions 返回 {"action": "done"} → 退出循环
5. Orch 读取 audit_log.json → 展示结果 → 用户终审
6. 用户确认 → 交付；用户有意见 → 更新 spec.json → 从步骤 2 重新开始
```

---

## 与 orchestrator-codebuddy 的关系

| 维度 | orchestrator-codebuddy | worker-checker |
|------|----------------------|-------------------|
| Worker 粒度 | 一个 Worker 做全部 | 拆成多个最小 Couple |
| 调度层 | Orch 直接调度 | loop.py 生成指令 → Orch 机械执行 |
| 任务拆解 | Orch 拆 | PM Couple 拆 |
| 并行 | 不支持 | 同层 Couple 并行 |
| Worker 工具集 | Team Mode | Team Mode + 强制写文件 |
| 文件交接 | 无要求 | 强制文件路径传递，loop.py + Orch 都不读内容 |
| Orch 权限 | 全权 | 翻译 spec + 机械执行 next_actions + 展示结果 |
| 防作弊 | 基础（上下文隔离 + judge） | 多层（loop 隔离 + 权限矩阵 + state 校验 + 审计） |
| 适用场景 | 简单单步任务 | 多步复杂任务 |
