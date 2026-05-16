#!/usr/bin/env python3
"""调用 AI 核验三级分类材料别称候选。支持多线程并发。"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


流程目录 = Path(__file__).resolve().parents[1]
数据目录 = 流程目录 / "数据"
导出目录 = 流程目录 / "导出"
默认候选JSONL = 数据目录 / "AI三级分类别称候选.jsonl"
默认核验JSONL = 数据目录 / "AI三级分类别称核验结果.jsonl"
默认核验CSV = 导出目录 / "AI三级分类别称核验结果.csv"

spec = importlib.util.spec_from_file_location("ai_tools", Path(__file__).with_name("通用AI工具.py"))
ai_tools = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(ai_tools)


SYSTEM_PROMPT = """
你是中国建筑工程材料采购标准化审核专家。请核验 AI 生成的材料别称候选是否可以回填到 CCE 三级分类材料库。

通过标准：
1. 别称必须是真实施工/采购中可能出现的材料叫法；
2. 别称必须能稳定指向目标材料，或稳定指向目标三级分类；
3. 不能只是规格、型号、性能等级本身；
4. 不能是动作、工序、服务、机械、人工；
5. 过于宽泛、可能指向多个不同材料的，必须 reject，除非目标类型是 category。

直接输出 JSON，不要任何开场白、分析过程、总结或解释。第一条字符必须是 {。

输出结构：
{
  "task_id": "",
  "checked": [
    {
      "target_type": "category|material",
      "target_name": "",
      "alias": "",
      "decision": "accept|reject",
      "confidence": "high|medium|low",
      "reason": ""
    }
  ]
}
"""


def 候选转列表(task: dict) -> list[dict]:
    data = task.get("result") or {}
    rows = []
    for row in data.get("category_aliases", []) or []:
        rows.append({
            "target_type": "category",
            "target_name": data.get("category_l3", ""),
            "alias": row.get("alias", ""),
            "generator_confidence": row.get("confidence", ""),
            "generator_reason": row.get("reason", ""),
        })
    for material in data.get("materials", []) or []:
        for row in material.get("aliases", []) or []:
            rows.append({
                "target_type": "material",
                "target_name": material.get("material_name", ""),
                "alias": row.get("alias", ""),
                "generator_confidence": row.get("confidence", ""),
                "generator_reason": row.get("reason", ""),
            })
    return [r for r in rows if r["alias"]]


def 写CSV(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["任务ID", "末级分类ID", "分类路径", "对象类型", "材料名称", "别称", "决定", "置信度", "理由"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def 导出核验CSV(jsonl_path: Path, csv_path: Path) -> None:
    rows = []
    for task in ai_tools.读JSONL(jsonl_path):
        if task.get("status") != "ok":
            continue
        for row in task.get("checked", []) or []:
            rows.append({
                "任务ID": task.get("task_id", ""),
                "末级分类ID": task.get("category_id", ""),
                "分类路径": task.get("category_path", ""),
                "对象类型": row.get("target_type", ""),
                "材料名称": row.get("target_name", ""),
                "别称": row.get("alias", ""),
                "决定": row.get("decision", ""),
                "置信度": row.get("confidence", ""),
                "理由": row.get("reason", ""),
            })
    写CSV(csv_path, rows)


def 规范化核验(parsed, candidates: list[dict]) -> list[dict]:
    if isinstance(parsed, list):
        parsed = next((x for x in parsed if isinstance(x, dict)), {})
    if not isinstance(parsed, dict):
        parsed = {}
    candidate_keys = {
        (c["target_type"], c["target_name"], ai_tools.归一(c["alias"]))
        for c in candidates
    }
    checked = []
    for row in parsed.get("checked", []) or []:
        target_type = str(row.get("target_type", "")).strip()
        target_name = str(row.get("target_name", "")).strip()
        alias = str(row.get("alias", "")).strip()
        if (target_type, target_name, ai_tools.归一(alias)) not in candidate_keys:
            continue
        decision = str(row.get("decision", "reject")).strip().lower()
        if decision not in {"accept", "reject"}:
            decision = "reject"
        confidence = str(row.get("confidence", "medium")).strip().lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"
        checked.append({
            "target_type": target_type,
            "target_name": target_name,
            "alias": alias,
            "decision": decision,
            "confidence": confidence,
            "reason": str(row.get("reason", "")).strip(),
        })
    return checked


# ── 单个核验任务（供线程池调用）──
def _执行核验任务(task: dict, args) -> dict:
    """核验一个分类的别称候选，返回结果行。线程安全。"""
    task_id = task.get("task_id", "")
    alias_rows = 候选转列表(task)
    if not alias_rows:
        return {
            "task_id": task_id,
            "status": "ok",
            "category_id": task.get("category_id", ""),
            "category_path": task.get("category_path", ""),
            "checked": [],
        }

    user_payload = {
        "task_id": task_id,
        "category_id": task.get("category_id", ""),
        "category_path": task.get("category_path", ""),
        "candidates": alias_rows,
    }
    started = datetime.now().isoformat(timespec="seconds")
    try:
        last_exc = None
        parsed = None
        for attempt in range(1, max(1, args.重试次数) + 1):
            try:
                raw = ai_tools.调用AI(
                    SYSTEM_PROMPT,
                    json.dumps(user_payload, ensure_ascii=False),
                    max_tokens=args.最大输出token,
                    temperature=args.温度,
                    thinking=not args.no_thinking,
                )
                parsed = ai_tools.提取JSON(raw)
                break
            except Exception as exc:
                last_exc = exc
        if parsed is None:
            raise last_exc or RuntimeError("AI 调用失败")
        checked = 规范化核验(parsed, alias_rows)
        return {
            "task_id": task_id,
            "status": "ok",
            "started_at": started,
            "category_id": task.get("category_id", ""),
            "category_path": task.get("category_path", ""),
            "checked": checked,
        }
    except Exception as exc:
        return {
            "task_id": task_id,
            "status": "error",
            "started_at": started,
            "category_id": task.get("category_id", ""),
            "category_path": task.get("category_path", ""),
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 核验三级分类材料别称候选（多线程）")
    parser.add_argument("--候选JSONL", default=str(默认候选JSONL))
    parser.add_argument("--输出JSONL", default=str(默认核验JSONL))
    parser.add_argument("--输出CSV", default=str(默认核验CSV))
    parser.add_argument("--线程数", type=int, default=5)
    parser.add_argument("--请求间隔秒", type=float, default=0.0)
    parser.add_argument("--重试次数", type=int, default=3)
    parser.add_argument("--最大输出token", type=int, default=2500)
    parser.add_argument("--温度", type=float, default=1.0)
    parser.add_argument("--覆盖", action="store_true")
    parser.add_argument("--no-thinking", dest="no_thinking", action="store_true", default=True,
                        help="禁用深度思考模式（默认开启，核验不需要深度思考可大幅提速）")
    parser.add_argument("--thinking", dest="no_thinking", action="store_false",
                        help="启用深度思考模式")
    args = parser.parse_args()

    # 只处理有别名产生的候选
    all_candidates = [t for t in ai_tools.读JSONL(Path(args.候选JSONL))
                      if t.get("status") == "ok" and t.get("alias_count", 0) > 0]

    output = Path(args.输出JSONL)
    done = set()
    if output.exists() and not args.覆盖:
        done = {row.get("task_id") for row in ai_tools.读JSONL(output) if row.get("status") == "ok"}

    pending = [t for t in all_candidates if t.get("task_id") not in done]

    thinking_mode = "disabled" if args.no_thinking else "enabled"
    print(f"AI别称核验: total={len(all_candidates)}, done={len(done)}, pending={len(pending)}, "
          f"threads={args.线程数}, thinking={thinking_mode}", flush=True)

    if not pending:
        print("所有核验已完成，无需执行。", flush=True)
        导出核验CSV(output, Path(args.输出CSV))
        return 0

    completed = 0
    failed = 0
    accepted = 0
    rejected = 0
    lock = threading.Lock()
    all_results = []

    with ThreadPoolExecutor(max_workers=args.线程数) as executor:
        futures = {executor.submit(_执行核验任务, t, args): t for t in pending}
        for future in as_completed(futures):
            task = futures[future]
            task_id = task["task_id"]
            try:
                row = future.result()
            except Exception as exc:
                row = {
                    "task_id": task_id,
                    "status": "error",
                    "category_id": task.get("category_id", ""),
                    "category_path": task.get("category_path", ""),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            all_results.append(row)

            with lock:
                completed += 1
                if row["status"] == "ok":
                    acc = sum(1 for r in row.get("checked", []) if r["decision"] == "accept")
                    rej = sum(1 for r in row.get("checked", []) if r["decision"] == "reject")
                    accepted += acc
                    rejected += rej
                else:
                    failed += 1
                print(f"[{completed}/{len(pending)}] {task_id} "
                      f"{'accept='+str(acc)+' reject='+str(rej) if row['status']=='ok' else 'FAILED: '+row.get('error','')[:60]} "
                      f"(ok:{completed-failed} err:{failed})", flush=True)

    for row in all_results:
        ai_tools.追加JSONL(output, row)

    导出核验CSV(output, Path(args.输出CSV))
    print(f"\n完成: pending={len(pending)}, ok={completed-failed}, err={failed}, "
          f"accept={accepted}, reject={rejected}", flush=True)
    print(f"核验JSONL: {output}", flush=True)
    print(f"核验CSV: {args.输出CSV}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
