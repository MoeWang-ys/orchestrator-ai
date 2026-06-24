#!/usr/bin/env python3
"""
loop.py v2 — Worker-Checker Couple 纯代码调度器

基于 task_graph.json 驱动多 Couple 并行执行。
生成 next_actions.json 供 Orch（信使）机械执行。
不调用任何 LLM，不启动任何子 Agent。

用法:
    python3 scripts/loop.py [--spec spec.json] [--graph task_graph.json]
    python3 scripts/loop.py --continue
    python3 scripts/loop.py --dry-run

输入:
    run_output/task_graph.json — 任务图（PM Couple 产出或手动编写）
    run_output/spec.json       — 用户需求（如无 task_graph，自动拆解）
    run_output/orch_receipt.json — Orch 执行回执（--continue 时）

输出:
    run_output/next_actions.json — 调度指令
    run_output/state.json        — 状态机位置
    run_output/audit_log.json    — 审计日志
"""

import json
import hashlib
import os
import sys
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

# ═══════════════════════════════════════════════════════════
# 路径常量
# ═══════════════════════════════════════════════════════════
RUN_OUTPUT = Path("run_output")
STATE_FILE = RUN_OUTPUT / "state.json"
ACTIONS_FILE = RUN_OUTPUT / "next_actions.json"
LOCK_FILE = RUN_OUTPUT / "next_actions.lock"
RECEIPT_FILE = RUN_OUTPUT / "orch_receipt.json"
AUDIT_FILE = RUN_OUTPUT / "audit_log.json"
TASK_GRAPH_FILE = RUN_OUTPUT / "task_graph.json"
SPEC_FILE = RUN_OUTPUT / "spec.json"
CHECKSUM_FILE = Path("scripts/checksum.txt")
JUDGE_PATH = Path("scripts/judge.py")
MAX_ROUNDS = 3
MAX_RETRIES_PER_WORKER = 3

# ═══════════════════════════════════════════════════════════
# 平台默认 agent_config — 部署时修改此处
# ═══════════════════════════════════════════════════════════
DEFAULT_AGENT_CONFIG = {
    "type": "subagent",
    "name": "code-explorer",
    "permission": "acceptEdits",
}


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_json_atomic(path: Path, data: Any):
    """原子写入：先 .tmp 再 rename。"""
    tmp = Path(str(path) + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def save_json_locked(path: Path, data: Any):
    """带 .lock 保护的原子写入。"""
    lock = Path(str(path) + ".lock")
    lock.write_text("")
    try:
        save_json_atomic(path, data)
    finally:
        if lock.exists():
            lock.unlink()


def file_ready(path: str) -> bool:
    """文件存在且不是 .tmp 后缀。"""
    p = Path(path)
    return p.exists() and p.suffix != ".tmp"


def compute_action_hash(actions: list) -> str:
    return hashlib.sha256(
        json.dumps(actions, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ═══════════════════════════════════════════════════════════
# Checksum 自检
# ═══════════════════════════════════════════════════════════

def verify_checksums() -> bool:
    if not CHECKSUM_FILE.exists():
        return True  # 没有 checksum 文件则不校验
    expected = load_json(CHECKSUM_FILE)
    for script_name in ["scripts/loop.py", "scripts/judge.py"]:
        if not Path(script_name).exists():
            continue
        actual = hashlib.sha256(Path(script_name).read_bytes()).hexdigest()
        if expected.get(script_name) != actual:
            print(f"TAMPER_DETECTED: {script_name} checksum mismatch", file=sys.stderr)
            return False
    return True


# ═══════════════════════════════════════════════════════════
# 审计日志
# ═══════════════════════════════════════════════════════════

def log_audit(entry: dict):
    audit = {"spec_ref": str(SPEC_FILE), "task_graph_ref": str(TASK_GRAPH_FILE), "entries": []}
    if AUDIT_FILE.exists():
        audit = load_json(AUDIT_FILE)
    entry["timestamp"] = now_iso()
    audit.setdefault("entries", []).append(entry)
    save_json_atomic(AUDIT_FILE, audit)


def log_tamper(reason: str):
    entry = {"event": "TAMPER_DETECTED", "reason": reason}
    log_audit(entry)
    print(f"FATAL: {reason}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════
# Orch 回执校验
# ═══════════════════════════════════════════════════════════

def verify_orch_receipt(expected_actions: list) -> bool:
    """校验 Orch 是否诚实执行了指令。"""
    if not RECEIPT_FILE.exists():
        log_tamper("orch_receipt.json not found")
        return False

    receipt = load_json(RECEIPT_FILE)
    expected_hash = compute_action_hash(expected_actions)

    if receipt.get("actions_hash") != expected_hash:
        log_tamper(f"action_hash mismatch: receipt={receipt.get('actions_hash', '?')[:12]} vs expected={expected_hash[:12]}")
        return False

    for action in expected_actions:
        if action["action"] != "start_worker":
            continue
        executed = next(
            (e for e in receipt.get("executed", []) if e["action_id"] == action["action_id"]),
            None,
        )
        if not executed:
            log_tamper(f"action {action['action_id']} not found in receipt")
            return False
        if action.get("output_file") and action["output_file"] not in executed.get("actual_params", {}).get("prompt", ""):
            log_tamper(f"output_file missing from prompt in action {action['action_id']}")
            return False

    return True


# ═══════════════════════════════════════════════════════════
# 拓扑排序检测依赖环
# ═══════════════════════════════════════════════════════════

def verify_task_graph(graph: dict) -> bool:
    """校验 task_graph 结构合法性，检测依赖环。"""
    try:
        layers = graph.get("layers", [])
        all_ids = set()
        depends_graph = {}

        for layer in layers:
            for couple in layer.get("couples", []):
                cid = couple["couple_id"]
                if cid in all_ids:
                    print(f"ERROR: duplicate couple_id: {cid}", file=sys.stderr)
                    return False
                all_ids.add(cid)
                deps = couple.get("prod_worker", {}).get("depends_on", [])
                depends_graph[cid] = deps

        # DFS 检测环
        visited = set()
        in_stack = set()

        def has_cycle(node: str) -> bool:
            if node not in depends_graph:
                return False
            visited.add(node)
            in_stack.add(node)
            for dep in depends_graph.get(node, []):
                # dep 是文件路径，需要反向查找哪个 couple 产出它
                for cid, deps in depends_graph.items():
                    # 简化：只检查 couple_id 直接的依赖
                    pass
                if dep in in_stack:
                    return True
                if dep not in visited:
                    if has_cycle(dep):
                        return True
            in_stack.discard(node)
            return False

        for cid in depends_graph:
            if cid not in visited:
                if has_cycle(cid):
                    print(f"ERROR: dependency cycle detected involving {cid}", file=sys.stderr)
                    return False

        return True
    except Exception as e:
        print(f"ERROR: task_graph validation failed: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════
# 状态机
# ═══════════════════════════════════════════════════════════

def get_state() -> dict:
    if STATE_FILE.exists():
        return load_json(STATE_FILE)
    return {"phase": "init", "layer": 0, "step": "", "round": 0, "retries": {}}


def save_state(state: dict):
    save_json_atomic(STATE_FILE, state)


def validate_state_transition(old_state: dict, new_state: dict) -> bool:
    """确保状态不跳 phase/step。"""
    valid_transitions = {
        "init": ["pm_prod", "executing_layer", "done"],
        "pm_prod": ["pm_check", "failed"],
        "pm_check": ["pm_judge", "failed"],
        "pm_judge": ["executing_layer", "failed"],
        "executing_layer": ["executing_layer", "done", "failed"],
        "done": [],
        "failed": [],
    }
    old_phase = old_state.get("phase", "init")
    new_phase = new_state.get("phase", "init")
    if new_phase not in valid_transitions.get(old_phase, []):
        print(f"WARNING: invalid state transition {old_phase} -> {new_phase}", file=sys.stderr)
        return False
    return True


# ═══════════════════════════════════════════════════════════
# PM Couple — 任务自动拆解（无 LLM 规则引擎）
# ═══════════════════════════════════════════════════════════

PM_SYSTEM_PROMPT = """你是 PM Couple 的生产 Worker。你的职责是根据用户需求描述，生成一个结构化的任务图（task_graph.json）。

任务图由多个"层（layer）"组成，层之间串行执行（上层全部完成才进入下层）。
每层内包含多个"Couple"，每个 Couple = 1 个生产 Worker + 1 个检查 Worker + 1 次 judge.py。

拆解原则：
1. 单一重复：一个 Worker 只做一类事
2. 文件交接：Worker 产出写入文件，通过文件路径传递
3. 同层可并行：同层 Couple 之间无数据依赖时标记 parallel=true
4. 每层必须带 judge 闸门

输出格式：
{
  "spec_ref": "run_output/spec.json",
  "layers": [
    {
      "layer": 1,
      "parallel": true,
      "couples": [
        {
          "couple_id": "唯一标识",
          "prod_worker": {
            "name": "prod-worker-xxx",
            "task": "任务描述（不含验收标准）",
            "output_file": "run_output/xxx.json",
            "depends_on": [],
            "timeout_seconds": 300
          },
          "check_worker": {
            "name": "check-worker-xxx",
            "checklist": ["检查项1", "检查项2"],
            "output_file": "run_output/check_xxx.json",
            "timeout_seconds": 180
          },
          "criteria": {
            "hard_blocks": [],
            "checks": [{"type": "all_items_pass", "value": true}],
            "logic": "all"
          }
        }
      ]
    }
  ]
}

只输出 JSON，不要其他内容。"""


def build_pm_prompt(spec: dict) -> str:
    """构建 PM 生产 Worker 的 prompt。"""
    return f"""## 用户需求

{json.dumps(spec, ensure_ascii=False, indent=2)}

## 要求

请将以上需求拆解为 task_graph.json 格式的任务图。
仔细分析哪些步骤可以并行，哪些必须串行。
每个步骤都要有明确的检查项和验收标准。
输出 task_graph.json 内容。"""


# ═══════════════════════════════════════════════════════════
# 简化拆解器（无 LLM 回退）
# ═══════════════════════════════════════════════════════════

def auto_decompose(spec: dict) -> dict:
    """
    无 LLM 的简化拆解器。
    将需求描述按段落拆分为独立 task，每个 task 作为一个 Couple。
    适合简单场景。复杂场景建议用 PM Couple（LLM）拆解。
    """
    title = spec.get("title", "Unnamed Task")
    description = spec.get("description", "")
    constraints = spec.get("constraints", [])

    # 按双换行拆分子任务
    subtasks = [t.strip() for t in description.split("\n\n") if t.strip()]
    if len(subtasks) <= 1:
        # 无法自动拆解 → 单 Couple
        subtasks = [description]

    layers = []
    for i, task_text in enumerate(subtasks):
        couple_id = f"task-{i+1}"
        layers.append({
            "layer": i + 1,
            "parallel": False,  # 简化模式逐层串行
            "couples": [{
                "couple_id": couple_id,
                "prod_worker": {
                    "name": f"prod-worker-{couple_id}",
                    "task": f"{task_text}\n\n约束条件：{constraints}" if constraints else task_text,
                    "output_file": f"run_output/{couple_id}_result.json",
                    "depends_on": [f"run_output/task-{i}_result.json"] if i > 0 else [],
                    "timeout_seconds": 300,
                },
                "check_worker": {
                    "name": f"check-worker-{couple_id}",
                    "checklist": [
                        "产出是否符合任务描述要求",
                        "输出是否为有效 JSON 格式",
                        "是否遵守了所有约束条件" if constraints else "基本完成度",
                    ],
                    "output_file": f"run_output/check_{couple_id}.json",
                    "timeout_seconds": 180,
                },
                "criteria": {
                    "hard_blocks": [
                        {"type": "schema", "value": {"required": []}},
                    ],
                    "checks": [
                        {"type": "all_items_pass", "value": True},
                    ],
                    "logic": "all",
                },
            }],
        })

    return {
        "spec_ref": str(SPEC_FILE),
        "layers": layers,
        "_auto_decomposed": True,
    }


# ═══════════════════════════════════════════════════════════
# Worker Prompt 构建
# ═══════════════════════════════════════════════════════════

PROD_SYSTEM_PROMPT = """你是生产 Worker。你的唯一职责是按任务描述产出结果，写入指定文件。
规则：
1. 只做任务描述里要求的事，不自我评价，不自我审查
2. 完成后将结果写入 {output_file}.tmp，写完后 rename 为 {output_file}
3. 返回一句话摘要和文件路径
4. 不输出验收标准、不评价自己的产出"""

CHECK_SYSTEM_PROMPT = """你是检查 Worker。你的唯一职责是按检查项逐条审查产出结果，给出客观评分。
规则：
1. 逐条对照检查项，不遗漏任何一条
2. 评分客观严格，不手软也不刁难
3. 不知道"多少分算过"——你只管打分
4. 完成后将结果写入 {output_file}.tmp，写完后 rename 为 {output_file}
5. 返回一句话摘要和文件路径

输出 JSON 格式：
{
  "results": [
    {"item_id": "check_N", "dimension": "维度", "score": 8, "max_score": 10, "pass": true, "notes": "依据"}
  ],
  "overall": {"total_score": 0, "max_total": 0, "pass_rate": 0, "summary": ""}
}"""


def build_prod_prompt(prod_config: dict, feedback: dict = None) -> str:
    """构建生产 Worker 的 prompt。"""
    parts = [f"## 任务\n\n{prod_config['task']}"]
    parts.append(f"\n## 输出文件\n\n完成后写入: {prod_config['output_file']}")

    if prod_config.get("depends_on"):
        parts.append(f"\n## 依赖文件（已就绪）\n")
        for f in prod_config["depends_on"]:
            parts.append(f"- {f}")

    if feedback:
        parts.append(f"\n## 上一轮反馈（必须修复）\n\n{json.dumps(feedback, ensure_ascii=False, indent=2)}")
        parts.append("\n请根据反馈修复问题，重新产出。")

    return "\n".join(parts)


def build_check_prompt(checklist: list, prod_output_file: str) -> str:
    """构建检查 Worker 的 prompt。"""
    parts = [
        "## 检查项\n\n逐条检查以下内容：\n",
    ]
    for i, item in enumerate(checklist, 1):
        parts.append(f"{i}. {item}")

    parts.append(f"\n## 需要审查的文件\n\n{prod_output_file}")
    parts.append("\n## 要求\n\n1. 逐条对照检查项，给出分数和依据\n2. 严格按 JSON 格式输出")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════
# Next Actions 生成
# ═══════════════════════════════════════════════════════════

def generate_next_actions(
    task_graph: dict,
    state: dict,
    feedback_map: dict = None,
) -> dict:
    """
    根据 task_graph 和当前 state，生成下一步的 next_actions.json。
    """
    feedback_map = feedback_map or {}
    phase = state.get("phase", "init")
    layer_idx = state.get("layer", 0)
    step = state.get("step", "")
    round_num = state.get("round", 0)
    retries = state.get("retries", {})

    # ── init: 检查是否有 task_graph ──
    if phase == "init":
        if not TASK_GRAPH_FILE.exists():
            # 尝试从 spec.json 自动拆解
            if SPEC_FILE.exists():
                spec = load_json(SPEC_FILE)
                task_graph = auto_decompose(spec)
                save_json_atomic(TASK_GRAPH_FILE, task_graph)
                print(f"Auto-decomposed spec into {len(task_graph['layers'])} layers", file=sys.stderr)
            else:
                return {"action": "done", "message": "No task_graph.json or spec.json found"}

        task_graph = load_json(TASK_GRAPH_FILE)
        if not verify_task_graph(task_graph):
            save_state({"phase": "failed", "layer": 0, "step": "", "round": 0, "retries": {}})
            return {"action": "done", "message": "task_graph validation failed"}

        layers = task_graph.get("layers", [])
        if not layers:
            save_state({"phase": "done", "layer": 0, "step": "", "round": 0, "retries": {}})
            return {"action": "done", "message": "No layers in task_graph"}

        # 检查是否有 PM Couple 标记（手动写的 task_graph 不需要 PM 拆解）
        if task_graph.get("_needs_pm"):
            new_state = {"phase": "pm_prod", "layer": 0, "step": "prod_workers", "round": 1, "retries": {}}
            save_state(new_state)
            return generate_pm_actions(task_graph, new_state)
        else:
            # 直接进入第一层执行
            new_state = {"phase": "executing_layer", "layer": 1, "step": "prod_workers", "round": 1, "retries": {}}
            save_state(new_state)
            return generate_layer_actions(task_graph, new_state, feedback_map)

    # ── PM 阶段 ──
    if phase == "pm_prod":
        return generate_pm_actions(task_graph, state)

    if phase == "pm_check":
        return generate_pm_check_actions(task_graph, state)

    if phase == "pm_judge":
        return generate_pm_judge_actions(task_graph, state)

    # ── executing_layer ──
    if phase == "executing_layer":
        return generate_layer_actions(task_graph, state, feedback_map)

    # ── done / failed ──
    return {"action": "done", "message": f"Phase: {phase}"}


def generate_pm_actions(task_graph: dict, state: dict) -> dict:
    """生成 PM Couple 的生产 action。"""
    spec = load_json(SPEC_FILE) if SPEC_FILE.exists() else {}
    prompt = build_pm_prompt(spec)
    actions = [{
        "action": "start_worker",
        "action_id": "pm-prod",
        "name": "pm-prod-worker",
        "agent_config": DEFAULT_AGENT_CONFIG,
        "prompt": prompt + f"\n\n完成后将结果写入 {RUN_OUTPUT}/task_graph.json.tmp，写完后 rename 为 {RUN_OUTPUT}/task_graph.json，然后返回摘要。",
        "output_file": str(TASK_GRAPH_FILE),
        "couple_id": "pm-couple",
        "timeout_seconds": 600,
    }]
    return {
        "action_hash": compute_action_hash(actions),
        "state": state,
        "actions": actions,
        "on_complete": "运行 python3 scripts/loop.py --continue",
    }


def generate_pm_check_actions(task_graph: dict, state: dict) -> dict:
    """生成 PM Couple 的检查 action。"""
    actions = [{
        "action": "start_worker",
        "action_id": "pm-check",
        "name": "pm-check-worker",
        "agent_config": DEFAULT_AGENT_CONFIG,
        "prompt": f"""## 检查任务图

请审查 {TASK_GRAPH_FILE} 文件内容，检查：
1. 是否所有 layer 都包含有效的 couples
2. 每个 couple 的 prod_worker 和 check_worker 配置是否完整
3. 每层是否有 judge 闸门（criteria）
4. depends_on 是否指向有效的产出文件
5. 是否有明显的依赖环

完成后将结果写入 {RUN_OUTPUT}/check_pm.json.tmp，写完后 rename 为 {RUN_OUTPUT}/check_pm.json，返回摘要。""",
        "output_file": f"{RUN_OUTPUT}/check_pm.json",
        "couple_id": "pm-couple",
        "timeout_seconds": 300,
    }]
    return {
        "action_hash": compute_action_hash(actions),
        "state": state,
        "actions": actions,
        "on_complete": "运行 python3 scripts/loop.py --continue",
    }


def generate_pm_judge_actions(task_graph: dict, state: dict) -> dict:
    """生成 PM judge action。"""
    criteria = {
        "pass_conditions": [
            {"id": "pm_check_pass", "type": "all_items_pass", "source": "results",
             "operator": "==", "value": True, "description": "PM 检查全部通过"},
        ],
        "logic": "all",
        "hard_blocks": ["pm_check_pass"],
    }
    actions = [{
        "action": "run_judge",
        "action_id": "pm-judge",
        "criteria": criteria,
        "check_file": f"{RUN_OUTPUT}/check_pm.json",
        "couple_id": "pm-couple",
    }]
    return {
        "action_hash": compute_action_hash(actions),
        "state": state,
        "actions": actions,
        "on_complete": "运行 python3 scripts/loop.py --continue",
    }


def generate_layer_actions(task_graph: dict, state: dict, feedback_map: dict) -> dict:
    """生成当前层的执行 actions。"""
    layers = task_graph.get("layers", [])
    layer_idx = state.get("layer", 1)
    step = state.get("step", "prod_workers")
    round_num = state.get("round", 1)
    retries = state.get("retries", {})

    if layer_idx > len(layers):
        return {"action": "done", "message": "All layers complete"}

    layer = layers[layer_idx - 1]
    couples = layer.get("couples", [])

    # ── prod_workers step ──
    if step == "prod_workers":
        actions = []
        for couple in couples:
            prod = couple["prod_worker"]
            cid = couple["couple_id"]
            feedback = feedback_map.get(cid)

            # 检查依赖文件是否就绪
            deps_ready = all(file_ready(d) for d in prod.get("depends_on", []))
            if not deps_ready and not feedback:
                # 依赖未就绪 → 等待
                missing = [d for d in prod.get("depends_on", []) if not file_ready(d)]
                actions.append({
                    "action": "wait_files",
                    "action_id": f"layer{layer_idx}-wait-{cid}",
                    "files": missing,
                    "timeout_seconds": 30,
                })
                continue

            prompt = build_prod_prompt(prod, feedback)
            actions.append({
                "action": "start_worker",
                "action_id": f"layer{layer_idx}-prod-{cid}",
                "name": prod["name"],
                "agent_config": DEFAULT_AGENT_CONFIG,
                "prompt": prompt + f"\n\n完成后将结果写入 {prod['output_file']}.tmp，写完后 rename 为 {prod['output_file']}，然后返回一句话摘要和文件路径。",
                "output_file": prod["output_file"],
                "couple_id": cid,
                "timeout_seconds": prod.get("timeout_seconds", 300),
            })

        new_state = {
            "phase": "executing_layer",
            "layer": layer_idx,
            "step": "prod_workers",
            "round": round_num,
            "retries": retries,
        }
        save_state(new_state)
        return {
            "action_hash": compute_action_hash(actions),
            "state": new_state,
            "actions": actions,
            "on_complete": "运行 python3 scripts/loop.py --continue",
        }

    # ── check_workers step ──
    if step == "check_workers":
        actions = []
        for couple in couples:
            prod = couple["prod_worker"]
            check = couple["check_worker"]
            cid = couple["couple_id"]

            if not file_ready(prod["output_file"]):
                # 生产 Worker 产出未就绪
                actions.append({
                    "action": "wait_files",
                    "action_id": f"layer{layer_idx}-wait-check-{cid}",
                    "files": [prod["output_file"]],
                    "timeout_seconds": 30,
                })
                continue

            prompt = build_check_prompt(check["checklist"], prod["output_file"])
            actions.append({
                "action": "start_worker",
                "action_id": f"layer{layer_idx}-check-{cid}",
                "name": check["name"],
                "agent_config": DEFAULT_AGENT_CONFIG,
                "prompt": prompt + f"\n\n完成后将结果写入 {check['output_file']}.tmp，写完后 rename 为 {check['output_file']}，然后返回摘要。",
                "output_file": check["output_file"],
                "couple_id": cid,
                "timeout_seconds": check.get("timeout_seconds", 180),
            })

        new_state = {
            "phase": "executing_layer",
            "layer": layer_idx,
            "step": "check_workers",
            "round": round_num,
            "retries": retries,
        }
        save_state(new_state)
        return {
            "action_hash": compute_action_hash(actions),
            "state": new_state,
            "actions": actions,
            "on_complete": "运行 python3 scripts/loop.py --continue",
        }

    # ── judge step ──
    if step == "judge":
        actions = []
        for couple in couples:
            check = couple["check_worker"]
            criteria = couple.get("criteria", {})
            cid = couple["couple_id"]

            if not file_ready(check["output_file"]):
                actions.append({
                    "action": "wait_files",
                    "action_id": f"layer{layer_idx}-wait-judge-{cid}",
                    "files": [check["output_file"]],
                    "timeout_seconds": 30,
                })
                continue

            # 转换 criteria 格式到 judge.py 可接受的格式
            judge_criteria = convert_criteria(criteria)

            actions.append({
                "action": "run_judge",
                "action_id": f"layer{layer_idx}-judge-{cid}",
                "criteria": judge_criteria,
                "check_file": check["output_file"],
                "couple_id": cid,
            })

        new_state = {
            "phase": "executing_layer",
            "layer": layer_idx,
            "step": "judge",
            "round": round_num,
            "retries": retries,
        }
        save_state(new_state)
        return {
            "action_hash": compute_action_hash(actions),
            "state": new_state,
            "actions": actions,
            "on_complete": "运行 python3 scripts/loop.py --continue",
        }

    # ── layer_done: 检查本层结果 ──
    if step == "layer_done":
        return handle_layer_done(task_graph, state)

    return {"action": "done", "message": f"Unknown step: {step}"}


def convert_criteria(criteria: dict) -> dict:
    """将 task_graph 中的 criteria 转换为 judge.py 的 pass_conditions 格式。"""
    pass_conditions = []
    hard_blocks = []

    # 处理 hard_blocks
    for block in criteria.get("hard_blocks", []):
        cond = {
            "id": f"hard_{block['type']}",
            "type": block["type"],
            "operator": block.get("op", "=="),
            "value": block["value"],
            "description": f"Hard block: {block['type']}",
        }
        if block["type"] == "threshold":
            cond["source"] = f"overall.{block.get('field', 'pass_rate')}"
        elif block["type"] == "schema":
            cond["source"] = "$"
        elif block["type"] == "all_items_pass":
            cond["source"] = "results"
        pass_conditions.append(cond)
        hard_blocks.append(cond["id"])

    # 处理 checks
    for check in criteria.get("checks", []):
        cond = {
            "id": f"check_{len(pass_conditions)}",
            "type": check["type"],
            "operator": "==",
            "value": check["value"],
            "description": f"Check: {check['type']}",
        }
        if check["type"] == "all_items_pass":
            cond["source"] = "results"
        pass_conditions.append(cond)

    return {
        "pass_conditions": pass_conditions,
        "logic": criteria.get("logic", "all"),
        "hard_blocks": hard_blocks,
    }


def handle_layer_done(task_graph: dict, state: dict) -> dict:
    """检查当前层所有 Couple 的 judge 结果，决定推进或重试。"""
    layers = task_graph.get("layers", [])
    layer_idx = state.get("layer", 1)
    round_num = state.get("round", 1)
    retries = state.get("retries", {})

    layer = layers[layer_idx - 1]
    couples = layer.get("couples", [])

    # 收集本层所有 judge 结果
    all_pass = True
    failed_couples = []
    feedback_map = {}

    for couple in couples:
        cid = couple["couple_id"]
        check_file = couple["check_worker"]["output_file"]
        criteria = couple.get("criteria", {})

        if not file_ready(check_file):
            all_pass = False
            failed_couples.append(cid)
            continue

        # 运行 judge.py
        try:
            judge_result = run_judge_sync(convert_criteria(criteria), check_file, cid, layer_idx, round_num)
            if not judge_result.get("pass", False):
                all_pass = False
                failed_couples.append(cid)
                feedback_map[cid] = {
                    "judge_summary": judge_result.get("summary", ""),
                    "failed_checks": [
                        {"item_id": d["id"], "reason": d["detail"]}
                        for d in judge_result.get("details", []) if not d.get("passed", False)
                    ],
                }
        except Exception as e:
            all_pass = False
            failed_couples.append(cid)
            feedback_map[cid] = {"error": str(e)}

    # 审计记录
    log_audit({
        "event": "layer_judge",
        "layer": layer_idx,
        "round": round_num,
        "all_pass": all_pass,
        "failed_couples": failed_couples,
    })

    if all_pass:
        # 进入下一层
        next_layer = layer_idx + 1
        if next_layer > len(layers):
            save_state({"phase": "done", "layer": layer_idx, "step": "", "round": round_num, "retries": {}})
            return {"action": "done", "message": f"All {len(layers)} layers passed"}
        else:
            new_state = {"phase": "executing_layer", "layer": next_layer, "step": "prod_workers", "round": 1, "retries": {}}
            save_state(new_state)
            return generate_layer_actions(task_graph, new_state, {})

    # 有失败的 → 重试或终止
    if round_num >= MAX_ROUNDS:
        save_state({"phase": "failed", "layer": layer_idx, "step": "", "round": round_num, "retries": retries})
        return {"action": "done", "message": f"Layer {layer_idx} failed after {MAX_ROUNDS} rounds. Failed: {failed_couples}"}

    # 重试
    new_state = {
        "phase": "executing_layer",
        "layer": layer_idx,
        "step": "prod_workers",
        "round": round_num + 1,
        "retries": retries,
    }
    save_state(new_state)
    return generate_layer_actions(task_graph, new_state, feedback_map)


def run_judge_sync(criteria: dict, check_file: str, couple_id: str, layer: int, round_num: int) -> dict:
    """同步运行 judge.py，返回判定结果。"""
    import subprocess

    criteria_path = RUN_OUTPUT / f"_judge_criteria_{couple_id}.json"
    save_json(criteria_path, criteria)

    result = subprocess.run(
        ["python3", str(JUDGE_PATH), str(criteria_path), check_file],
        capture_output=True, text=True, timeout=30,
    )

    if result.returncode == 2:
        raise RuntimeError(f"judge.py error: {result.stderr}")

    judge_result = json.loads(result.stdout)

    # 记录到审计
    log_audit({
        "event": "judge_complete",
        "layer": layer,
        "round": round_num,
        "couple_id": couple_id,
        "judge_result": "PASS" if judge_result.get("pass") else "FAIL",
        "summary": judge_result.get("summary", ""),
    })

    return judge_result


# ═══════════════════════════════════════════════════════════
# 清理残留 .tmp 文件
# ═══════════════════════════════════════════════════════════

def cleanup_stale_tmp():
    """清理超过 10 分钟的 .tmp 残留文件。"""
    now = time.time()
    for tmp in RUN_OUTPUT.rglob("*.tmp"):
        try:
            age = now - tmp.stat().st_mtime
            if age > 600:
                tmp.unlink()
                print(f"Cleaned stale tmp: {tmp}", file=sys.stderr)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Worker-Checker Couple v2 纯代码调度器"
    )
    parser.add_argument("--spec", help="spec.json 路径（初始启动时使用）")
    parser.add_argument("--graph", help="task_graph.json 路径（已有任务图时使用）")
    parser.add_argument("--continue", dest="continue_mode", action="store_true",
                        help="继续推进状态机（Orch 执行完一批后调用）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将要生成的 actions，不写入文件")
    args = parser.parse_args()

    # Checksum 自检
    if not verify_checksums():
        sys.exit(1)

    # 清理残留
    cleanup_stale_tmp()

    # ── 初始启动 ──
    if args.spec:
        spec = load_json(Path(args.spec))
        save_json_atomic(SPEC_FILE, spec)
        print(f"Spec loaded: {spec.get('title', 'Untitled')}", file=sys.stderr)

    if args.graph:
        graph = load_json(Path(args.graph))
        if not verify_task_graph(graph):
            print("ERROR: task_graph validation failed")
            sys.exit(1)
        save_json_atomic(TASK_GRAPH_FILE, graph)
        print(f"Task graph loaded: {len(graph['layers'])} layers", file=sys.stderr)

    # ── continue 模式：校验 orch_receipt ──
    if args.continue_mode:
        if not ACTIONS_FILE.exists():
            print("ERROR: no next_actions.json found, run without --continue first")
            sys.exit(1)

        prev_actions_data = load_json(ACTIONS_FILE)
        prev_actions = prev_actions_data.get("actions", [])

        if not args.dry_run and not verify_orch_receipt(prev_actions):
            print("FATAL: orch_receipt verification failed. Terminating.", file=sys.stderr)
            sys.exit(1)

        # 根据上一步 action 推进状态机
        state = get_state()
        if state["step"] == "prod_workers":
            state["step"] = "check_workers"
        elif state["step"] == "check_workers":
            state["step"] = "judge"
        elif state["step"] == "judge":
            state["step"] = "layer_done"
        elif state["step"] == "layer_done":
            pass  # handle_layer_done 自己处理
        save_state(state)

    # ── 生成 next_actions ──
    state = get_state()
    task_graph = load_json(TASK_GRAPH_FILE) if TASK_GRAPH_FILE.exists() else None

    if task_graph is None and SPEC_FILE.exists():
        # 无 task_graph → 自动拆解
        spec = load_json(SPEC_FILE)
        task_graph = auto_decompose(spec)
        save_json_atomic(TASK_GRAPH_FILE, task_graph)
        print(f"Auto-decomposed: {len(task_graph['layers'])} layers", file=sys.stderr)

    if task_graph is None:
        print("ERROR: no task_graph.json or spec.json found in run_output/")
        sys.exit(1)

    next_actions = generate_next_actions(task_graph, state)

    if args.dry_run:
        print("=== DRY RUN: next_actions.json ===", file=sys.stderr)
        print(json.dumps(next_actions, ensure_ascii=False, indent=2))
        return 0

    # 写入 next_actions.json（带 lock 保护）
    save_json_locked(ACTIONS_FILE, next_actions)
    print(json.dumps(next_actions, ensure_ascii=False, indent=2))

    action_type = next_actions.get("action", "?")
    if action_type == "done":
        print("LOOP_RESULT: DONE")
        return 0

    action_count = len(next_actions.get("actions", []))
    print(f"LOOP_RESULT: ACTIONS | count={action_count} | phase={state['phase']} | layer={state['layer']} | step={state['step']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
