#!/usr/bin/env python3
"""按 CCE 三级分类调用 AI 生成材料别称/俗称候选。支持多线程并发。"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


流程目录 = Path(__file__).resolve().parents[1]
数据目录 = 流程目录 / "数据"
导出目录 = 流程目录 / "导出"
默认材料库 = 数据目录 / "三级分类材料库.json"
默认候选JSONL = 数据目录 / "AI三级分类别称候选.jsonl"
默认候选CSV = 导出目录 / "AI三级分类别称候选.csv"

spec = importlib.util.spec_from_file_location("ai_tools", Path(__file__).with_name("通用AI工具.py"))
ai_tools = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(ai_tools)


SYSTEM_PROMPT = """
你是中国建筑工程材料采购分类专家，任务是为 CCE 标准三级分类下的标准材料品种补充行业别称、俗称、简称、施工现场口语叫法。

硬性要求：
1. 只能为输入中给出的 category_path 和 standard_materials 推理别称；不要改变标准材料名。
2. 别称必须是真实采购/施工中可能出现的叫法，不能是解释性短语，不能是规格参数本身。
3. 如果一个叫法会同时指向多个材料，不要强行挂到某个材料，放到 category_aliases。
4. 不确定就少给，不能为了凑数量编造。
5. 直接输出 JSON，不要任何开场白、分析过程、总结或解释。第一条字符必须是 {。

输出 JSON 结构：
{
  "category_id": "",
  "category_path": "",
  "chunk_index": 1,
  "category_aliases": [
    {"alias": "", "reason": "", "confidence": "high|medium|low"}
  ],
  "materials": [
    {
      "material_name": "",
      "aliases": [
        {"alias": "", "reason": "", "confidence": "high|medium|low"}
      ]
    }
  ],
  "possible_new_materials": [
    {"material_name": "", "reason": "", "confidence": "high|medium|low"}
  ]
}
"""


def 写CSV(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["任务ID", "末级分类ID", "分类路径", "对象类型", "材料名称", "别称", "置信度", "理由"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def 选择分类(kb: dict, args) -> list[dict]:
    categories = [c for c in kb.get("categories", []) if c.get("material_count", 0) > 0]
    if args.all:
        selected = categories
    elif args.category_ids:
        ids = set(args.category_ids)
        selected = [c for c in categories if str(c.get("category_id")) in ids]
    elif args.category_name:
        keyword = args.category_name.strip()
        selected = [
            c for c in categories
            if keyword in c.get("category_l3", "") or keyword in c.get("category_path", "")
        ]
    else:
        selected = categories
    if args.limit > 0:
        selected = selected[:args.limit]
    return selected


def 切片(rows: list[dict], size: int) -> list[list[dict]]:
    size = max(1, size)
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def 规范化结果(parsed, category: dict, chunk_index: int, materials: list[dict]) -> dict:
    if isinstance(parsed, list):
        parsed = next((x for x in parsed if isinstance(x, dict)), {})
    if not isinstance(parsed, dict):
        parsed = {}
    allowed = {m.get("material_name", "") for m in materials}
    result = {
        "category_id": str(category.get("category_id", "")),
        "category_path": category.get("category_path", ""),
        "category_l3": category.get("category_l3", ""),
        "chunk_index": chunk_index,
        "category_aliases": [],
        "materials": [],
        "possible_new_materials": [],
    }
    for row in parsed.get("category_aliases", []) or []:
        alias = str(row.get("alias", "")).strip()
        if alias:
            result["category_aliases"].append({
                "alias": alias,
                "reason": str(row.get("reason", "")).strip(),
                "confidence": str(row.get("confidence", "medium")).strip() or "medium",
            })
    for row in parsed.get("materials", []) or []:
        name = str(row.get("material_name", "")).strip()
        if name not in allowed:
            continue
        aliases = []
        for alias_row in row.get("aliases", []) or []:
            alias = str(alias_row.get("alias", "")).strip()
            if alias:
                aliases.append({
                    "alias": alias,
                    "reason": str(alias_row.get("reason", "")).strip(),
                    "confidence": str(alias_row.get("confidence", "medium")).strip() or "medium",
                })
        if aliases:
            result["materials"].append({"material_name": name, "aliases": aliases})
    for row in parsed.get("possible_new_materials", []) or []:
        name = str(row.get("material_name", "")).strip()
        if name:
            result["possible_new_materials"].append({
                "material_name": name,
                "reason": str(row.get("reason", "")).strip(),
                "confidence": str(row.get("confidence", "low")).strip() or "low",
            })
    return result


def 导出候选CSV(jsonl_path: Path, csv_path: Path) -> None:
    rows = []
    for task in ai_tools.读JSONL(jsonl_path):
        if task.get("status") != "ok":
            continue
        data = task.get("result") or {}
        category_id = data.get("category_id", "")
        category_path = data.get("category_path", "")
        task_id = task.get("task_id", "")
        for row in data.get("category_aliases", []) or []:
            rows.append({
                "任务ID": task_id,
                "末级分类ID": category_id,
                "分类路径": category_path,
                "对象类型": "category",
                "材料名称": data.get("category_l3", ""),
                "别称": row.get("alias", ""),
                "置信度": row.get("confidence", ""),
                "理由": row.get("reason", ""),
            })
        for material in data.get("materials", []) or []:
            for row in material.get("aliases", []) or []:
                rows.append({
                    "任务ID": task_id,
                    "末级分类ID": category_id,
                    "分类路径": category_path,
                    "对象类型": "material",
                    "材料名称": material.get("material_name", ""),
                    "别称": row.get("alias", ""),
                    "置信度": row.get("confidence", ""),
                    "理由": row.get("reason", ""),
                })
    写CSV(csv_path, rows)


# ── 单个任务执行函数（供线程池调用）──
def _执行单个任务(task_info: dict, args) -> dict:
    """处理一个分类chunk的AI别称生成，返回结果行。线程安全。"""
    category = task_info["category"]
    chunk_index = task_info["chunk_index"]
    materials = task_info["materials"]
    task_id = task_info["task_id"]

    user_payload = {
        "category_id": category.get("category_id", ""),
        "category_path": category.get("category_path", ""),
        "chunk_index": chunk_index,
        "standard_materials": [m.get("material_name", "") for m in materials],
        "instruction": "请为这些标准材料名补充真实别称/俗称。没有可靠别称的材料返回空 aliases。",
    }

    sys.stdout.flush()  # 立即显示

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
                # 重试前不等太久，并发时让其他线程继续
        if parsed is None:
            raise last_exc or RuntimeError("AI 调用失败")
        result = 规范化结果(parsed, category, chunk_index, materials)
        alias_count = len(result["category_aliases"]) + sum(len(m["aliases"]) for m in result["materials"])
        return {
            "task_id": task_id,
            "status": "ok",
            "started_at": started,
            "category_id": category.get("category_id", ""),
            "category_path": category.get("category_path", ""),
            "result": result,
            "alias_count": alias_count,
        }
    except Exception as exc:
        return {
            "task_id": task_id,
            "status": "error",
            "started_at": started,
            "category_id": category.get("category_id", ""),
            "category_path": category.get("category_path", ""),
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 按三级分类生成材料别称候选（多线程）")
    parser.add_argument("--材料库", default=str(默认材料库))
    parser.add_argument("--输出JSONL", default=str(默认候选JSONL))
    parser.add_argument("--输出CSV", default=str(默认候选CSV))
    parser.add_argument("--分类ID", dest="category_ids", nargs="*", default=[])
    parser.add_argument("--分类名称", dest="category_name", default="")
    parser.add_argument("--全部", dest="all", action="store_true")
    parser.add_argument("--限制", dest="limit", type=int, default=0)
    parser.add_argument("--每批材料数", type=int, default=25)
    parser.add_argument("--线程数", type=int, default=5)
    parser.add_argument("--请求间隔秒", type=float, default=0.0)
    parser.add_argument("--重试次数", type=int, default=3)
    parser.add_argument("--最大输出token", type=int, default=2500)
    parser.add_argument("--温度", type=float, default=1.0)
    parser.add_argument("--覆盖", action="store_true")
    parser.add_argument("--no-thinking", dest="no_thinking", action="store_true", default=True,
                        help="禁用深度思考模式（默认开启，别称生成不需要深度思考可大幅提速）")
    parser.add_argument("--thinking", dest="no_thinking", action="store_false",
                        help="启用深度思考模式")
    args = parser.parse_args()

    kb = ai_tools.读JSON(Path(args.材料库))
    categories = 选择分类(kb, args)
    output = Path(args.输出JSONL)

    # 读取已完成的 task_id
    done = set()
    if output.exists() and not args.覆盖:
        done = {row.get("task_id") for row in ai_tools.读JSONL(output) if row.get("status") == "ok"}

    # 构建所有待处理任务
    pending_tasks = []
    for category in categories:
        chunks = 切片(category.get("materials", []), args.每批材料数)
        for chunk_index, materials in enumerate(chunks, start=1):
            task_id = f"{category.get('category_id')}-{chunk_index}"
            if task_id in done:
                continue
            pending_tasks.append({
                "category": category,
                "chunk_index": chunk_index,
                "materials": materials,
                "task_id": task_id,
            })

    total_tasks = len(pending_tasks) + len(done)
    thinking_mode = "disabled" if args.no_thinking else "enabled"
    print(f"AI别称候选生成: categories={len(categories)}, total_tasks={total_tasks}, "
          f"done={len(done)}, pending={len(pending_tasks)}, threads={args.线程数}, thinking={thinking_mode}",
          flush=True)

    if not pending_tasks:
        print("所有任务已完成，无需执行。", flush=True)
        导出候选CSV(output, Path(args.输出CSV))
        return 0

    # 多线程并发执行
    completed = 0
    failed = 0
    alias_total = 0
    lock = threading.Lock()
    all_results = []

    with ThreadPoolExecutor(max_workers=args.线程数) as executor:
        futures = {executor.submit(_执行单个任务, t, args): t for t in pending_tasks}
        for future in as_completed(futures):
            task = futures[future]
            task_id = task["task_id"]
            try:
                row = future.result()
            except Exception as exc:
                row = {
                    "task_id": task_id,
                    "status": "error",
                    "started_at": datetime.now().isoformat(timespec="seconds"),
                    "category_id": task["category"].get("category_id", ""),
                    "category_path": task["category"].get("category_path", ""),
                    "error": f"{type(exc).__name__}: {exc}",
                }

            all_results.append(row)

            with lock:
                completed += 1
                if row["status"] == "ok":
                    alias_total += row.get("alias_count", 0)
                else:
                    failed += 1
                print(f"[{completed}/{len(pending_tasks)}] {task_id} "
                      f"{'aliases=' + str(row.get('alias_count',0)) if row['status']=='ok' else 'FAILED: ' + row.get('error','')[:60]} "
                      f"(ok:{completed-failed} err:{failed})", flush=True)

    # 将所有结果追加到 JSONL（保留已有内容）
    for row in all_results:
        ai_tools.追加JSONL(output, row)

    导出候选CSV(output, Path(args.输出CSV))
    print(f"\n完成: pending={len(pending_tasks)}, ok={completed - failed}, err={failed}, "
          f"aliases={alias_total}", flush=True)
    print(f"候选JSONL: {output}", flush=True)
    print(f"候选CSV: {args.输出CSV}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
