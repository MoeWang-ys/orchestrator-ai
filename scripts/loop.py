#!/usr/bin/env python3
"""
loop.py — 全自动生产-检查循环控制器

调用 Claude CLI 分别启动生产 Worker 和检查 Worker，
用 judge.py 做比对，不通过则带反馈重试，直到通过或达到最大轮次。

用法:
    python3 loop.py --config <config.json> [--max-rounds 3] [--dry-run]

config.json 结构:
    {
      "task": { ... },           // task.json 内容
      "checklist": { ... },      // checklist.json 内容
      "criteria": { ... },       // criteria.json 内容
      "options": {
        "production_model": "haiku",
        "check_model": "opus",
        "max_rounds": 3,
        "output_dir": "run_output"
      }
    }

依赖:
    - claude CLI（需在 PATH 中）
    - judge.py（同目录或 PATH 中）

中间产物:
    每轮输出保存到 output_dir/round_N/ 目录:
      - production_prompt.txt     发给生产 Worker 的完整 prompt
      - production_result.json   生产 Worker 的输出
      - check_prompt.txt         发给检查 Worker 的完整 prompt
      - check_result.json        检查 Worker 的输出
      - judge_result.json        judge.py 的比对结果
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
JUDGE_PATH = SCRIPT_DIR / "judge.py"

def write_progress(output_dir: Path, **kwargs):
    """写入实时进度文件，供 status.py 读取。"""
    progress = {
        "started_at": kwargs.get("started_at", datetime.now(timezone.utc).isoformat()),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": kwargs.get("status", "running"),
        "current_round": kwargs.get("current_round"),
        "max_rounds": kwargs.get("max_rounds"),
        "phase": kwargs.get("phase"),
        "rounds": kwargs.get("rounds", []),
    }
    (output_dir / "progress.json").write_text(
        json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
    )


PRODUCTION_SYSTEM_PROMPT = """你是生产 Worker。你的唯一职责是按任务描述产出结果，不做额外的事。
规则：
1. 只做任务描述里要求的事，不自我评价，不自我审查
2. 严格按要求的 JSON 格式输出，不添加多余文字
3. 如果收到"上一轮反馈"，那是检查 Worker 发现的问题，认真修复
4. 只输出 JSON，不要用 markdown 代码块包装，不要加解释

输出示例格式：
{"code": "...", "functions": [...]}"""

CHECK_SYSTEM_PROMPT = """你是检查 Worker。你的唯一职责是按检查项逐条审查产出结果，给出客观评分。
规则：
1. 逐条对照检查项，不遗漏任何一条
2. 评分客观严格，不手软也不刁难
3. 不知道"多少分算过"——你只管打分
4. 只输出 JSON，不用 markdown 代码块包装，不加解释"""

CHECK_SCHEMA = {
    "type": "object",
    "required": ["results", "overall"],
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["item_id", "dimension", "score", "max_score", "pass", "notes"],
                "properties": {
                    "item_id": {"type": "string"},
                    "dimension": {"type": "string"},
                    "score": {"type": "number"},
                    "max_score": {"type": "number"},
                    "pass": {"type": "boolean"},
                    "notes": {"type": "string"}
                }
            }
        },
        "overall": {
            "type": "object",
            "required": ["total_score", "max_total", "pass_rate", "summary"],
            "properties": {
                "total_score": {"type": "number"},
                "max_total": {"type": "number"},
                "pass_rate": {"type": "number"},
                "summary": {"type": "string"}
            }
        }
    }
}


def _extract_json(text: str) -> dict:
    """从文本中提取 JSON，处理 markdown 代码块包装。"""
    # 清理 markdown ```json ... ``` 包装
    text = text.strip()
    if text.startswith("```"):
        # 移除 ```json 和结尾 ```
        lines = text.split("\n")
        # 去掉第一行（```json 或 ```）
        if lines[0].startswith("```"):
            lines = lines[1:]
        # 去掉最后一行（```）
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


def encode_image(path: str) -> str:
    """将图片文件编码为 data:image URI。"""
    import base64
    ext = Path(path).suffix.lower().lstrip(".")
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/png")
    data = base64.b64encode(Path(path).read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{data}"


def call_api(prompt: str, system_prompt: str, model: str,
             api_endpoint: str, api_key: str,
             output_dir: Path = None, label: str = "output",
             json_schema: dict = None, timeout: int = 600,
             images: list = None) -> dict:
    """调用 OpenAI 兼容 API，支持多模态（images 参数传图片路径列表）。"""
    import urllib.request
    import urllib.error

    if images:
        content = [{"type": "text", "text": prompt}]
        for img in images:
            uri = encode_image(img) if Path(img).exists() else img
            content.append({"type": "image_url", "image_url": {"url": uri}})
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}]
    else:
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
    body = {"model": model, "messages": messages, "temperature": 0.3}
    if json_schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "output", "schema": json_schema}}

    req = urllib.request.Request(
        api_endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )

    print(f"  [{label}] 调用 API --model {model} ...")
    start = time.time()

    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API HTTP {e.code}: {e.read().decode('utf-8')[:500]}")
    except Exception as e:
        raise RuntimeError(f"API 调用失败: {e}")

    elapsed = time.time() - start
    content = data["choices"][0]["message"]["content"]
    print(f"  [{label}] 耗时 {elapsed:.1f}s, tokens={data.get('usage', {})}")

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"{label}_api_response.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return _extract_json(content)


def call_claude(prompt: str, system_prompt: str, model: str = "haiku",
                output_dir: Path = None, label: str = "output",
                json_schema: dict = None, bare: bool = True,
                timeout: int = 600) -> dict:
    """调用 Claude CLI，返回 Worker 的实际输出（已从外层 JSON 提取 result 字段）。

    bare=True:  最小模式，无网络，Worker 只能基于 prompt 内容工作。
    bare=False: 完整模式，Worker 可以 WebSearch/WebFetch，适合需要在线信息的任务。
    """
    cmd = [
        "claude", "-p", prompt,
        "--model", model,
        "--system-prompt", system_prompt,
        "--output-format", "json",
        "--no-session-persistence",
        "--permission-mode", "auto",
    ]
    if bare:
        cmd.append("--bare")
    if json_schema:
        cmd += ["--json-schema", json.dumps(json_schema)]

    print(f"  [{label}] 启动 claude --model {model} ...")
    start = time.time()

    # 准备实时日志文件
    live_log = None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        live_log = open(output_dir / f"{label}_live.log", "w", encoding="utf-8")
        live_log.write(f"=== SYSTEM ===\n{system_prompt}\n\n=== PROMPT ===\n{prompt}\n\n=== OUTPUT ===\n")
        live_log.flush()

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
        env={**__import__("os").environ, "CLAUDE_CODE_SIMPLE": "1"},
    )

    # 实时读取 stdout，同时写入 live_log
    stdout_lines = []
    for line in proc.stdout:
        stdout_lines.append(line)
        if live_log:
            live_log.write(line)
            live_log.flush()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        if live_log:
            live_log.write(f"\n[TIMEOUT] 超过 {timeout}s\n")
            live_log.close()
        raise RuntimeError(f"Claude CLI 超时 ({timeout}s)")

    elapsed = time.time() - start
    stdout_text = "".join(stdout_lines)
    stderr_text = proc.stderr.read() if proc.stderr else ""
    print(f"  [{label}] 耗时 {elapsed:.1f}s, exit_code={proc.returncode}")

    if live_log:
        live_log.write(f"\n=== EXIT: {proc.returncode} | ELAPSED: {elapsed:.1f}s ===\n")
        live_log.close()

    if output_dir:
        (output_dir / f"{label}_prompt.txt").write_text(
            f"=== SYSTEM ===\n{system_prompt}\n\n=== PROMPT ===\n{prompt}", encoding="utf-8"
        )
        (output_dir / f"{label}_raw.txt").write_text(
            f"STDOUT:\n{stdout_text}\n\nSTDERR:\n{stderr_text}", encoding="utf-8"
        )

    if proc.returncode != 0:
        error_msg = stderr_text.strip()[:500]
        raise RuntimeError(f"Claude CLI 返回非零 exit code {proc.returncode}: {error_msg}")

    # 解析外层 JSON（Claude CLI 的 --output-format json 格式）
    try:
        cli_response = json.loads(stdout_text)
    except json.JSONDecodeError:
        raise RuntimeError(f"无法解析 Claude CLI 响应为 JSON: {stdout_text[:500]}")

    # 提取 result 字段（LLM 的实际输出文本）
    raw_result = cli_response.get("result", "")
    if not raw_result:
        raise RuntimeError("Claude CLI 响应中 'result' 字段为空")

    # 保存原始输出
    if output_dir:
        (output_dir / f"{label}_raw_result.txt").write_text(raw_result, encoding="utf-8")

    # 尝试将 result 解析为 JSON
    try:
        data = _extract_json(raw_result)
    except (json.JSONDecodeError, ValueError) as e:
        raise RuntimeError(f"无法从 Worker 输出中提取 JSON: {e}\n原始输出前 500 字符: {raw_result[:500]}")

    if output_dir:
        (output_dir / f"{label}_result.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return data


def build_production_prompt(task: dict, feedback: dict = None) -> str:
    """构建生产 Worker 的 prompt。"""
    parts = [f"## 任务描述\n\n{task['description']}"]

    if task.get("output_format"):
        fmt = task["output_format"]
        parts.append(f"\n## 输出格式要求\n\n{fmt.get('description', '')}")
        if fmt.get("schema"):
            parts.append(f"\n输出 JSON Schema:\n```json\n{json.dumps(fmt['schema'], ensure_ascii=False, indent=2)}\n```")
        if fmt.get("example"):
            parts.append(f"\n输出示例:\n```json\n{json.dumps(fmt['example'], ensure_ascii=False, indent=2)}\n```")

    if task.get("context", {}).get("constraints"):
        parts.append(f"\n## 约束条件\n")
        for c in task["context"]["constraints"]:
            parts.append(f"- {c}")

    if feedback:
        parts.append(f"\n## 上一轮检查反馈（必须修复）\n\n{json.dumps(feedback, ensure_ascii=False, indent=2)}")
        parts.append("\n请根据反馈修复问题，重新产出结果。")

    return "\n".join(parts)


def build_check_prompt(checklist: dict, production_result: dict) -> str:
    """构建检查 Worker 的 prompt。"""
    parts = [
        "## 检查项\n\n逐条检查以下内容：\n",
        json.dumps(checklist["items"], ensure_ascii=False, indent=2),
        "\n## 生产 Worker 的输出\n\n以下是需要你审查的产出：\n",
        json.dumps(production_result, ensure_ascii=False, indent=2),
        "\n## 要求\n",
        "1. 逐条对照检查项，给出分数和依据",
        "2. 严格按 JSON 格式输出，字段一个不能少",
        "3. 只输出 JSON，不要其他内容",
    ]
    return "\n".join(parts)


def run_judge(criteria: dict, check_result: dict, output_dir: Path) -> dict:
    """运行 judge.py。"""
    criteria_path = output_dir / "_criteria.json"
    check_path = output_dir / "check_result.json"

    criteria_path.write_text(json.dumps(criteria, ensure_ascii=False, indent=2), encoding="utf-8")
    check_path.write_text(json.dumps(check_result, ensure_ascii=False, indent=2), encoding="utf-8")

    result = subprocess.run(
        ["python3", str(JUDGE_PATH), str(criteria_path), str(check_path)],
        capture_output=True, text=True, timeout=30,
    )

    if result.returncode == 2:
        raise RuntimeError(f"judge.py 执行错误: {result.stderr}")

    judge_result = json.loads(result.stdout)
    (output_dir / "judge_result.json").write_text(
        json.dumps(judge_result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return judge_result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Orchestrator 自动循环控制器")
    parser.add_argument("--config", required=True, help="config.json 路径")
    parser.add_argument("--max-rounds", type=int, default=None, help="最大重试轮次（覆盖 config 中的设置）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要执行的操作，不实际调用 Claude")
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    task = config["task"]
    checklist = config["checklist"]
    criteria = config["criteria"]
    options = config.get("options", {})

    max_rounds = args.max_rounds or criteria.get("max_rounds", options.get("max_rounds", 3))
    production_model = options.get("production_model", "haiku")
    check_model = options.get("check_model", "opus")
    check_backend = options.get("check_backend", "claude")
    production_backend = options.get("production_backend", "claude")
    api_endpoint = options.get("api_endpoint", "")
    api_key = options.get("api_key", "")
    bare = options.get("bare", True)
    worker_timeout = options.get("worker_timeout", 600)
    base_output_dir = Path(options.get("output_dir", "run_output"))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_output_dir = base_output_dir / timestamp
    base_output_dir.mkdir(parents=True, exist_ok=True)

    # 保存完整 config 供审计
    (base_output_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"═══ Orchestrator Loop 启动 ═══")
    print(f"最大轮次: {max_rounds}")
    print(f"生产模型: {production_model}")
    print(f"检查模型: {check_model} ({check_backend})")
    print(f"隔离模式: {'bare (无网络)' if bare else 'full (可搜索)'}")
    print(f"输出目录: {base_output_dir}")
    print()

    feedback = None
    rounds_summary = []

    for round_num in range(1, max_rounds + 1):
        print(f"─── 第 {round_num}/{max_rounds} 轮 ───")
        round_dir = base_output_dir / f"round_{round_num}"
        round_dir.mkdir(parents=True, exist_ok=True)

        round_record = {"round": round_num, "status": "running"}

        # ① 生产 Worker
        production_prompt = build_production_prompt(task, feedback)
        print(f"① 生产 Worker")
        write_progress(base_output_dir, status="running", current_round=round_num,
                       max_rounds=max_rounds, phase="production_worker",
                       rounds=rounds_summary)

        if args.dry_run:
            print(f"  [DRY RUN] 将发送 prompt ({len(production_prompt)} 字符)")
            production_result = {"dry_run": True, "round": round_num}
            round_record["production_ok"] = True
        else:
            try:
                prod_schema = task.get("output_format", {}).get("schema")
                if production_backend == "api":
                    production_result = call_api(
                        production_prompt, PRODUCTION_SYSTEM_PROMPT,
                        model=production_model, api_endpoint=api_endpoint, api_key=api_key,
                        output_dir=round_dir, label="production",
                        json_schema=prod_schema, timeout=worker_timeout
                    )
                else:
                    production_result = call_claude(
                        production_prompt, PRODUCTION_SYSTEM_PROMPT,
                        model=production_model, output_dir=round_dir, label="production",
                        json_schema=prod_schema, bare=bare, timeout=worker_timeout
                    )
                print(f"  产出: {json.dumps(production_result, ensure_ascii=False)[:200]}...")
                round_record["production_ok"] = True
            except Exception as e:
                print(f"  生产 Worker 失败: {e}")
                round_record["production_ok"] = False
                round_record["status"] = "error"
                round_record["error"] = str(e)
                rounds_summary.append(round_record)
                write_progress(base_output_dir, status="running", current_round=round_num,
                               max_rounds=max_rounds, phase="error", rounds=rounds_summary)
                continue

        # ② 检查 Worker
        check_prompt = build_check_prompt(checklist, production_result)
        # 提取生产输出中的图片路径，供多模态检查使用
        check_images = production_result.get("images", []) if isinstance(production_result, dict) else []
        print(f"② 检查 Worker" + (f" (多模态, {len(check_images)} 图)" if check_images else ""))
        write_progress(base_output_dir, status="running", current_round=round_num,
                       max_rounds=max_rounds, phase="check_worker",
                       rounds=rounds_summary)

        if args.dry_run:
            print(f"  [DRY RUN] 将发送 prompt ({len(check_prompt)} 字符)")
            check_result = {
                "results": [{"item_id": "dry", "dimension": "test", "score": 10, "max_score": 10, "pass": True, "notes": "dry run"}],
                "overall": {"total_score": 10, "max_total": 10, "pass_rate": 1.0, "summary": "dry run"}
            }
            round_record["check_ok"] = True
        else:
            try:
                if check_backend == "api":
                    check_result = call_api(
                        check_prompt, CHECK_SYSTEM_PROMPT,
                        model=check_model, api_endpoint=api_endpoint, api_key=api_key,
                        output_dir=round_dir, label="check",
                        json_schema=CHECK_SCHEMA, timeout=worker_timeout,
                        images=check_images if check_images else None
                    )
                else:
                    check_result = call_claude(
                        check_prompt, CHECK_SYSTEM_PROMPT,
                        model=check_model, output_dir=round_dir, label="check",
                        json_schema=CHECK_SCHEMA, bare=bare, timeout=worker_timeout
                    )
                print(f"  评分: total={check_result.get('overall', {}).get('total_score', '?')}/{check_result.get('overall', {}).get('max_total', '?')}")
                round_record["check_ok"] = True
            except Exception as e:
                print(f"  检查 Worker 失败: {e}")
                round_record["check_ok"] = False
                round_record["status"] = "error"
                round_record["error"] = str(e)
                rounds_summary.append(round_record)
                write_progress(base_output_dir, status="running", current_round=round_num,
                               max_rounds=max_rounds, phase="error", rounds=rounds_summary)
                continue

        # ③ judge.py 比对
        print(f"③ judge.py 比对")
        write_progress(base_output_dir, status="running", current_round=round_num,
                       max_rounds=max_rounds, phase="judging", rounds=rounds_summary)

        try:
            judge_result = run_judge(criteria, check_result, round_dir)
        except Exception as e:
            print(f"  judge.py 执行失败: {e}")
            round_record["status"] = "error"
            round_record["error"] = str(e)
            rounds_summary.append(round_record)
            continue

        if judge_result["pass"]:
            print(f"\n✅ 第 {round_num} 轮通过!")
            print(f"   {judge_result['summary']}")
            print(f"\n   最终产出: {round_dir / 'production_result.json'}")
            print(f"   完整审计: {base_output_dir}")
            print(f"\n⏸  等待人工终审确认...")
            round_record["status"] = "passed"
            round_record["judge_pass"] = True
            rounds_summary.append(round_record)
            write_progress(base_output_dir, status="completed", current_round=round_num,
                           max_rounds=max_rounds, phase="done", rounds=rounds_summary)
            (base_output_dir / "FINAL_PASS.json").write_text(json.dumps({
                "status": "passed",
                "round": round_num,
                "judge_result": judge_result,
                "output_dir": str(round_dir),
                "awaiting_human_approval": True,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"LOOP_RESULT: PASS | round={round_num} | passed={judge_result['passed_count']}/{judge_result['total']} | output={base_output_dir}")
            return 0

        # 未通过，准备反馈
        failed_items = [d for d in judge_result["details"] if not d["passed"]]
        print(f"  ❌ 未通过: {judge_result['summary']}")
        print(f"  失败项: {[d['id'] for d in failed_items]}")
        round_record["status"] = "failed"
        round_record["judge_pass"] = False
        round_record["phase"] = "retrying"
        rounds_summary.append(round_record)
        write_progress(base_output_dir, status="running", current_round=round_num,
                       max_rounds=max_rounds, phase="retrying", rounds=rounds_summary)

        feedback = {
            "round": round_num,
            "judge_summary": judge_result["summary"],
            "failed_checks": [
                {
                    "item_id": d["id"],
                    "reason": d["detail"],
                    "description": d.get("description", ""),
                }
                for d in failed_items
            ],
            "check_details": judge_result["details"],
        }

    # 达到最大轮次仍未通过
    print(f"\n❌ 达到最大轮次 ({max_rounds})，仍未通过")
    write_progress(base_output_dir, status="failed", current_round=max_rounds,
                   max_rounds=max_rounds, phase="max_rounds_reached", rounds=rounds_summary)
    (base_output_dir / "FINAL_FAIL.json").write_text(json.dumps({
        "status": "failed",
        "max_rounds": max_rounds,
        "last_feedback": feedback,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"LOOP_RESULT: FAIL | max_rounds={max_rounds} | output={base_output_dir}")
    print(f"   审计记录: {base_output_dir}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
