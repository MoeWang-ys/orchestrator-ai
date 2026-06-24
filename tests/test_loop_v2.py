#!/usr/bin/env python3
"""
测试 loop.py v2 完整流程（模拟 Orch 回执）。
"""
import json
import hashlib
import subprocess
import sys
from pathlib import Path

RUN_OUTPUT = Path("run_output")

def compute_hash(actions):
    return hashlib.sha256(
        json.dumps(actions, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()

def mock_receipt(actions_data):
    """模拟 Orch 写回执。"""
    actions = actions_data["actions"]
    receipt = {
        "timestamp": "2026-06-24T00:00:00Z",
        "actions_hash": actions_data["action_hash"],
        "executed": [],
    }
    for a in actions:
        receipt["executed"].append({
            "action_id": a["action_id"],
            "actual_params": {
                "name": a.get("name", ""),
                "permission": a.get("agent_config", {}).get("permission", "acceptEdits"),
                "agent_name": a.get("agent_config", {}).get("name", "code-explorer"),
                "prompt": a.get("prompt", ""),
            },
            "status": "completed",
            "duration_seconds": 1,
        })
    RECEIPT_FILE = RUN_OUTPUT / "orch_receipt.json"
    RECEIPT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT_FILE.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))

def run_loop(args):
    result = subprocess.run(
        ["python3", "scripts/loop.py"] + args,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: {result.stderr}")
        sys.exit(1)
    return json.loads(result.stdout.strip())

# 初始化
print("=== Step 1: Init with task_graph ===")
actions_data = run_loop(["--graph", "examples/sample-task-graph.json"])
print(f"  Phase: {actions_data['state']['phase']}, Layer: {actions_data['state']['layer']}, Step: {actions_data['state']['step']}")
print(f"  Actions: {[a['action_id'] for a in actions_data['actions']]}")
assert len(actions_data["actions"]) == 2, "Layer 1 should have 2 parallel prod workers"
print("  ✅ OK")

# 模拟 Orch 执行完 → 写回执 → continue
mock_receipt(actions_data)

print("\n=== Step 2: Continue to check_workers ===")
actions_data = run_loop(["--continue", "--dry-run"])
print(f"  Phase: {actions_data['state']['phase']}, Layer: {actions_data['state']['layer']}, Step: {actions_data['state']['step']}")
print(f"  Actions: {[a['action_id'] for a in actions_data['actions']]}")
assert actions_data["state"]["step"] == "check_workers"
print("  ✅ OK")

# 模拟 Orch 执行完 → continue to judge
mock_receipt(actions_data)

print("\n=== Step 3: Continue to judge ===")
actions_data = run_loop(["--continue", "--dry-run"])
print(f"  Phase: {actions_data['state']['phase']}, Layer: {actions_data['state']['layer']}, Step: {actions_data['state']['step']}")
print(f"  Actions: {[a['action_id'] for a in actions_data['actions']]}")
assert actions_data["state"]["step"] == "judge"
print("  ✅ OK")

print("\n=== All tests passed! ===")
