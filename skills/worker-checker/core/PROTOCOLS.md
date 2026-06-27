# Worker-Checker Couple · 协议定义

> **这是 worker-checker 的平台无关协议定义。**
> 本文档定义了所有 JSON 文件格式、Action 抽象类型、状态机、以及 loop.py 的接口契约。
> 适配器负责将抽象 Action 映射到具体平台的 API 调用。

---

## 文件清单

| 文件 | 方向 | 内容 |
|------|------|------|
| `spec.json` | Orch 写 → loop.py 读 | 用户需求的结构化描述 |
| `task_graph.json` | PM Couple 写 → loop.py 读 | PM Couple 产出的任务图 |
| `state.json` | loop.py 读/写 | 当前状态机位置（layer, round, step） |
| `next_actions.json` | loop.py 写 → Orch 读 | 下一步要执行的调度指令（含 action_hash） |
| `next_actions.lock` | loop.py 写时创建 | 防止 Orch 读到半截 JSON |
| `orch_receipt.json` | Orch 写 → loop.py 读 | Orch 执行完一批 action 后写的回执 |
| `audit_log.json` | loop.py 写 | 每轮执行记录 |
| `scripts/checksum.txt` | 部署时写入 | loop.py + judge.py 的 SHA256，启动时自检 |
| Worker 产出文件 | loop.py 不读 | loop.py 只检查文件是否存在（`Path.exists()`），忽略 `.tmp` 后缀 |

---

## spec.json 格式

```json
{
  "title": "任务标题",
  "description": "用户需求的自然语言描述",
  "constraints": ["约束1", "约束2"],
  "output_format": "期望的输出格式"
}
```

Orch 翻译用户需求为此格式，只描述"要什么"，不描述"怎么做"。

---

## task_graph.json 格式

```json
{
  "spec_ref": "run_output/spec.json",
  "layers": [
    {
      "layer": 1,
      "parallel": true,
      "couples": [
        {
          "couple_id": "search-github",
          "prod_worker": {
            "name": "prod-worker-github",
            "task": "获取 Zara Zhang 在 GitHub 上的公开仓库和 star 数据",
            "output_file": "run_output/data_github.json",
            "depends_on": [],
            "timeout_seconds": 300
          },
          "check_worker": {
            "name": "check-worker-github",
            "checklist": ["数据来源是否为 GitHub API", "stars 数字是否准确", "日期格式是否统一"],
            "output_file": "run_output/check_github.json",
            "timeout_seconds": 180
          },
          "criteria": {
            "hard_blocks": [
              {"type": "schema", "value": {"required": ["repos", "total_stars"]}},
              {"type": "threshold", "field": "total_stars", "value": 0, "op": "gt"}
            ],
            "checks": [
              {"type": "all_items_pass", "value": true}
            ],
            "logic": "all"
          }
        }
      ]
    }
  ]
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| `spec_ref` | 指向 spec.json 的路径 |
| `layers[].layer` | 层级编号，从 1 开始。层与层之间串行 |
| `layers[].parallel` | 该层内的 Couple 是否可并行执行 |
| `couples[].couple_id` | 唯一标识符 |
| `prod_worker.name` | Worker 名称（用于 Team Mode 命名） |
| `prod_worker.task` | 目标描述（纯目标，不含实现方式），Worker 自主决定如何实现 |
| `prod_worker.output_file` | 产出文件路径 |
| `prod_worker.depends_on` | 依赖文件列表 |
| `prod_worker.timeout_seconds` | 超时秒数（默认 300） |
| `check_worker.name` | 检查 Worker 名称 |
| `check_worker.checklist` | 检查清单（字符串数组） |
| `check_worker.output_file` | 打分文件路径 |
| `check_worker.timeout_seconds` | 超时秒数（默认 180） |
| `criteria.hard_blocks` | 硬性阻断条件（不满足则直接 FAIL） |
| `criteria.checks` | 检查项列表 |
| `criteria.logic` | 判定逻辑：`all` 全部通过 / `any` 任一通过 |

### Criteria 类型

**hard_blocks：**
- `{"type": "schema", "value": {"required": [...]}}` — JSON schema 校验
- `{"type": "regex", "field": "file", "pattern": "..."}` — 正则匹配文件内容
- `{"type": "threshold", "field": "...", "value": N, "op": "gt"}` — 阈值校验
- `{"type": "all_items_pass", "value": true}` — 所有检查项必须 PASS

---

## checklist 生成规则 🛑

> **checklist 从 spec.json 的目标推导，不从 Worker 的执行步骤推导。**

### 正确示例

```json
// spec.json 目标：遮盖图片中的姓名、工号、电话、邮箱
// ✅ 正确的 checklist：
[
  "图片中是否还有可见的人名（中文姓名）？扫描全图确认",
  "图片中是否还有可见的工号（数字串）？扫描全图确认",
  "图片中是否还有可见的电话号码？扫描全图确认",
  "图片中是否还有可见的邮箱地址？扫描全图确认",
  "非敏感区域（工具栏、表头）是否未被误遮盖？"
]
```

### 错误示例

```json
// ❌ 错误的 checklist —— 从 Worker 执行步骤推导：
[
  "x=222-393 是否已变白色？",
  "x=396-543 是否已变白色？",
  "Worker 是否完成了 4 个列的遮盖？"
]
```

### 规则

1. **checklist 从 spec 目标推导** — 阅读 spec.json 的 description 和 constraints，提取用户真正关心的验证点
2. **checklist 项必须可独立验证** — 不需要参考 Worker 的输出或日志就能判断
3. **不信任 Worker** — checklist 不应包含"Worker 是否做了 X"这类词，而是直接验证结果
4. **保持 spec 精度** — spec 要求遮盖姓名，checklist 就写"是否还有可见的人名"，不写"像素 x=222-393 是否白色"

## 检查 Worker 的输入

Checker 的输入不包含 Worker 的执行步骤、日志或任务描述。它只接收：

- **spec.json**（用户目标和约束）
- **产物文件路径**（Worker 交付的文件）

Checker 自行决定如何验证目标是否在产物中达成。

---

## retry_context.json 格式

> Judge 不通过时，loop.py 将重试上下文打包为 `retry_context.json`，供 Worker 下次执行时参考。

```json
{
  "spec": {
    "title": "遮盖 Excel 截图敏感列",
    "description": "将图片中的姓名、工号、联系电话、邮箱四列用白色马赛克遮盖",
    "constraints": ["保留工具栏", "保留表头行", "非敏感列不动"]
  },
  "original_file": "run_output/original.png",
  "last_output": "run_output/masked.png",
  "check_result": {
    "couple_id": "mask-columns",
    "worker": "check-worker-mask",
    "checklist_results": [
      {"item": "图片中是否还有可见的人名？", "result": "FAIL", "detail": "A 列（x=72-220）仍可见中文姓名"},
      {"item": "图片中是否还有可见的工号？", "result": "PASS", "detail": ""}
    ]
  },
  "judge_result": "FAIL",
  "judge_detail": "hard_block 'all_items_pass' 不满足: 1/5 项 FAIL"
}
```

| 字段 | 说明 |
|------|------|
| `spec` | 完整的 spec.json 内容（原始目标 + 约束） |
| `original_file` | 当前 Worker 修改前的原始文件路径 |
| `last_output` | Worker 上次交付的文件路径 |
| `check_result` | Checker 的各检查项结果（含具体失败原因） |
| `judge_result` | Judge 的最终判定 |
| `judge_detail` | 判定未通过的具体原因 |

### 重试流程

```
1. judge 输出 FAIL
2. loop.py 打包 retry_context.json（spec + 原始文件 + 上次产出 + 检查结果 + 判定）
3. loop.py round+1，生成新的 next_actions.json，将 retry_context.json 路径加入 Worker prompt
4. Worker 读取 retry_context.json，根据失败原因修正实现
5. Worker 写新文件
6. check-worker 再次检查
7. judge 重新判定
```

### Checker prompt 模板

```
你是检查 Worker。你的任务是验证以下目标是否在产物中达成。

## 用户需求（spec）
{spec}

## 约束条件
{constraints}

## 产物文件
{prod_output_file}

逐项检查 spec 中的每个目标是否在产物文件中实现，
将结果写入 {check_output_file}。
```

> **关键：Checker 不读 Worker 的执行日志，不验证 Worker 的步骤，只验证 spec 目标是否在产物中达成。**
> Checker 自行决定如何检查，不接收预设的 checklist。

---

## next_actions.json 格式

```json
{
  "action_hash": "sha256_of_actions_array",
  "state": {
    "phase": "executing_layer",
    "layer": 1,
    "step": "prod_workers",
    "round": 1
  },
  "actions": [
    {
      "action": "start_worker",
      "action_id": "layer1-prod-github",
      "name": "prod-worker-github",
      "agent_config": {
        "type": "subagent",
        "name": "code-explorer",
        "permission": "acceptEdits"
      },
      "prompt": "搜索 Zara Zhang GitHub 数据...\n完成后将结果写入 run_output/data_github.json.tmp，写完后 rename 为 run_output/data_github.json，然后返回一句话摘要和文件路径。",
      "output_file": "run_output/data_github.json",
      "couple_id": "search-github",
      "timeout_seconds": 300
    }
  ],
  "on_complete": "运行 loop.py --continue 推进状态"
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| `action_hash` | SHA256(JSON.stringify(actions数组))，用于校验 Orch 是否篡改 |
| `state.phase` | 当前状态机阶段 |
| `state.layer` | 当前层级 |
| `state.step` | 当前步骤 |
| `state.round` | 当前轮次（重试计数） |
| `actions[]` | 要执行的 action 列表 |
| `on_complete` | 完成后 Orch 应执行的操作 |

### agent_config 抽象

`agent_config` 是平台无关的子 Agent 启动配置。各平台适配器负责翻译：

```json
{
  "type": "subagent",
  "name": "code-explorer",
  "permission": "acceptEdits"
}
```

### 多平台版本

worker-checker 提供 4 个平台版本，各版本硬编码自己的 `agent_config`。loop.py 中的 `DEFAULT_AGENT_CONFIG` 在各版本部署时硬编码为对应平台的值。

---

## Action 抽象类型

| action | 含义 | Orch 做什么（抽象） | 必填字段 |
|--------|------|-------------------|---------|
| `start_worker` | 启动一个子 Agent 执行任务 | 启动子 Agent，传入 prompt 和 output_file | action_id, name, agent_config, prompt, output_file, couple_id, timeout_seconds |
| `run_judge` | 运行 judge.py | `execute_command("python3 scripts/judge.py ...")` | action_id, criteria, check_file, couple_id |
| `wait_files` | 等待文件就绪 | 检查文件是否存在，最多等 30 秒 | action_id, files[], timeout_seconds |
| `done` | 全部完成 | 读取 audit_log.json，展示结果 | — |

---

## orch_receipt.json 格式

```json
{
  "timestamp": "2026-06-22T00:00:00Z",
  "actions_hash": "sha256_of_executed_actions",
  "executed": [
    {
      "action_id": "layer1-prod-github",
      "actual_params": {
        "name": "prod-worker-github",
        "permission": "acceptEdits",
        "agent_name": "code-explorer",
        "prompt": "搜索 Zara Zhang GitHub 数据..."
      },
      "status": "completed",
      "duration_seconds": 45
    }
  ],
  "confession": {
    "breach": "描述越界行为（如果发生）",
    "which_breath_failed": "第几息断了",
    "timestamp": "ISO8601"
  }
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| `timestamp` | ISO8601 时间戳 |
| `actions_hash` | 执行 action 的 hash，与 next_actions.action_hash 比对 |
| `executed[].action_id` | 对应 next_actions 中的 action_id |
| `executed[].actual_params` | Orch 实际传给子 Agent 的参数（供 loop.py 校验） |
| `executed[].status` | `"completed"` / `"timeout"` |
| `executed[].duration_seconds` | 实际执行时长 |
| `confession` | **可选**。如果 Orch 越界了，在此字段记录（忏悔机制） |

---

## action_hash 校验机制

每个 `next_actions.json` 顶部带 `action_hash` = SHA256(JSON.stringify(actions数组))。

Orch 执行完一批后，loop.py `--continue` 会：

1. 读取 `orch_receipt.json`（Orch 写的执行参数快照）
2. 重新计算 `next_actions.json` 中 actions 的 hash
3. 比对：`orch_receipt.actions_hash` 是否等于 `next_actions.action_hash`
4. 不匹配 → 写入 audit_log 标记 `TAMPER_DETECTED` → 终止流程

---

## audit_log.json 格式

```json
{
  "spec_ref": "run_output/spec.json",
  "task_graph_ref": "run_output/task_graph.json",
  "entries": [
    {
      "layer": 1,
      "round": 1,
      "couple_id": "search-github",
      "prod_worker": "prod-worker-github",
      "prod_output": "run_output/data_github.json",
      "check_worker": "check-worker-github",
      "check_output": "run_output/check_github.json",
      "judge_result": "PASS",
      "truncation_recovery": false,
      "timestamp": "2026-06-21T23:50:00Z"
    }
  ]
}
```

---

## state.json 格式

```json
{
  "phase": "executing_layer",
  "layer": 1,
  "step": "prod_workers",
  "round": 1
}
```

---

## 状态机定义

```
PHASES = [
    "init",              # 初始状态，需要 task_graph.json
    "pm_prod",           # 启动 PM 生产 Worker
    "pm_check",          # 启动 PM 检查 Worker
    "pm_judge",          # PM judge 判定
    "executing_layer",   # 执行某一层
    "done",              # 全部完成
    "failed",            # 失败终止
]

STEPS = [
    "prod_workers",      # 启动该层所有生产 Worker
    "check_workers",     # 启动该层所有检查 Worker
    "judge",             # 运行 judge.py
    "layer_done",        # 检查该层结果，决定推进或重试
]
```

### 状态流转

```
init
  → pm_prod → pm_check → pm_judge
  → executing_layer (layer=1, step=prod_workers)
      → prod_workers → check_workers → judge → layer_done
          → 全部 PASS → next layer (layer+1)
          → 有 FAIL + round < 3 → 重试 (round+1)
          → 有 FAIL + round >= 3 → failed
  → done (全部层完成)
```

---

## 原子写入协议

所有文件写入必须遵循原子写入协议：

```
1. 写入 {path}.tmp
2. fsync / flush
3. rename {path}.tmp → {path}
```

loop.py 的 `file_ready()` 检查时忽略 `.tmp` 后缀文件。

loop.py 写 `next_actions.json` 时额外使用 `.lock` 文件：
```
1. 创建 next_actions.lock
2. 写入 next_actions.json（原子写入）
3. 删除 next_actions.lock
```

Orch 在读取 `next_actions.json` 前必须等待 `.lock` 不存在。

---

## loop.py 接口契约

loop.py 是一个纯 Python 脚本，不调用任何 LLM，不启动任何子 Agent。

### 命令行接口

```bash
python3 loop.py              # 初始启动（phase=init）
python3 loop.py --continue   # 继续推进状态机
```

### 输入

- `run_output/task_graph.json` — 任务图（PM Couple 产出）
- `run_output/state.json` — 当前状态
- `run_output/orch_receipt.json` — Orch 执行回执（`--continue` 时）
- `scripts/checksum.txt` — 自检校验和

### 输出

- `run_output/next_actions.json` — 调度指令（stdout 也打印）
- `run_output/state.json` — 更新后状态
- `run_output/audit_log.json` — 审计日志（追加）

### 自检

启动时必须：
1. 校验 `scripts/checksum.txt` 中 loop.py 和 judge.py 的 SHA256
2. 不匹配 → 输出 `FATAL: checksum verification failed` → `exit(1)`

### 纯代码校验

`--continue` 时必须：
1. 读取 `orch_receipt.json`，校验 `actions_hash`
2. 抽查 `actual_params.prompt` 是否包含 `output_file` 路径
3. 校验 state 连续性（不允许跳 phase/step）
4. 校验 task_graph 结构合法性（拓扑排序检测依赖环）

### loop.py 伪代码

```python
import json, sys, os, hashlib, time
from pathlib import Path

STATE_FILE = "run_output/state.json"
ACTIONS_FILE = "run_output/next_actions.json"
LOCK_FILE = "run_output/next_actions.lock"
RECEIPT_FILE = "run_output/orch_receipt.json"
AUDIT_FILE = "run_output/audit_log.json"
CHECKSUM_FILE = "scripts/checksum.txt"
MAX_ROUNDS = 3

def load_json(path):
    with open(path) as f:
        return json.load(f)

def save_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)

def get_state():
    if Path(STATE_FILE).exists():
        return load_json(STATE_FILE)
    return {"phase": "init", "layer": 0, "step": "", "round": 0}

def file_ready(path):
    p = Path(path)
    return p.exists() and not p.suffix == ".tmp"

def verify_checksums():
    if not Path(CHECKSUM_FILE).exists():
        return True
    expected = load_json(CHECKSUM_FILE)
    for script_name in ["loop.py", "scripts/judge.py"]:
        if not Path(script_name).exists():
            continue
        actual = hashlib.sha256(Path(script_name).read_bytes()).hexdigest()
        if expected.get(script_name) != actual:
            print(f"TAMPER_DETECTED: {script_name} checksum mismatch")
            return False
    return True

def verify_orch_receipt(expected_actions):
    if not Path(RECEIPT_FILE).exists():
        log_tamper("orch_receipt.json not found")
        return False
    receipt = load_json(RECEIPT_FILE)
    expected_hash = hashlib.sha256(
        json.dumps(expected_actions, sort_keys=True).encode()
    ).hexdigest()
    if receipt.get("actions_hash") != expected_hash:
        log_tamper(f"action_hash mismatch")
        return False
    for action in expected_actions:
        if action["action"] != "start_worker":
            continue
        executed = next(
            (e for e in receipt.get("executed", []) if e["action_id"] == action["action_id"]),
            None
        )
        if not executed:
            log_tamper(f"action {action['action_id']} not found in receipt")
            return False
        if action["output_file"] not in executed.get("actual_params", {}).get("prompt", ""):
            log_tamper(f"output_file missing from prompt")
            return False
    return True

def log_tamper(reason):
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": "TAMPER_DETECTED",
        "reason": reason
    }
    audit = load_json(AUDIT_FILE) if Path(AUDIT_FILE).exists() else {"entries": []}
    audit.setdefault("entries", []).append(entry)
    save_json_atomic(AUDIT_FILE, audit)

def compute_action_hash(actions):
    return hashlib.sha256(json.dumps(actions, sort_keys=True).encode()).hexdigest()

# ═══════════════════════════════════════════════════════════
# 平台默认 agent_config — 各平台部署时修改此处
# ═══════════════════════════════════════════════════════════
DEFAULT_AGENT_CONFIG = {
    "type": "subagent",
    "name": "code-explorer",
    "permission": "acceptEdits"
}

# ... 更多实现见完整 loop.py
```

### 平台适配：修改 DEFAULT_AGENT_CONFIG

```python
# CodeBuddy
DEFAULT_AGENT_CONFIG = {"type": "subagent", "name": "code-explorer", "permission": "acceptEdits"}

# Claude Code
DEFAULT_AGENT_CONFIG = {"type": "subagent", "name": "general-purpose", "permission": "acceptEdits"}

# Codex CLI
DEFAULT_AGENT_CONFIG = {"type": "subagent", "name": "general-purpose", "permission": "acceptEdits"}
```
