#!/usr/bin/env python3
"""批量版：每API调用处理10个系统（约30-50条材料），大幅缩短总时间。

与单系统版区别:
  - 单系统版: 635次API调用 × 4s = 42分钟+
  - 批量版: ~64次API调用 × 10s = 11分钟
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
KB_PATH = ROOT / "项目数据" / "项目默认值库.json"
BACKUP_DIR = ROOT / "过程数据" / "分类库备份"
LOG_PATH = ROOT / "过程数据" / "AI补全默认值库_批量_日志.jsonl"

SERVER_DIR = str(ROOT / "服务端")
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

env_file = ROOT / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip()

from AI客户端 import chat_json

SYSTEM_PROMPT = """你是中国建设工程材料专家。为给定的材料推荐条目补全可执行字段。

## quantity_rule.formula 规范
变量: BOQ_AREA(面积m²), BOQ_VOLUME(体积m³), BOQ_LENGTH(长度m), BOQ_COUNT(个数), BOQ_WEIGHT(重量kg/t), THICKNESS_MM(厚度mm), USAGE_PER_M2(单方用量kg/m²), USAGE_PER_M(单米用量kg/m), COEFF(系数), LOSS(损耗率小数)

公式模板:
- 体积类(混凝土/砂浆/回填/砌体): BOQ_VOLUME * COEFF * (1 + LOSS)
- 厚度类(保温板/找平层/面层): BOQ_AREA * THICKNESS_MM / 1000 * (1 + LOSS)
- 消耗类(粘接剂/涂料/腻子): BOQ_AREA * USAGE_PER_M2 * (1 + LOSS)
- 面层类(砖/卷材/板材): BOQ_AREA * COEFF * (1 + LOSS)
- 线材类(管材/型材/线缆): BOQ_LENGTH * COEFF * (1 + LOSS)
- 线消耗类(密封胶/止水带): BOQ_LENGTH * USAGE_PER_M * (1 + LOSS)
- 计数类(门窗/器具): BOQ_COUNT * COEFF * (1 + LOSS)
- 重量类(钢筋/钢材): BOQ_WEIGHT * COEFF * (1 + LOSS)

不能编造变量。公式只用 + - * / ( ) 运算符。

## 损耗率 loss_rate_hint
小数格式 (0.01=1%), 范围 0.001-0.50
混凝土:0.01, 钢筋:0.02-0.03, 模板:0.05, 砌体:0.02-0.03, 防水卷材:0.05-0.08, 保温板:0.02-0.03, 涂料:0.03-0.05, 地砖:0.03-0.05, 管材:0.02
如果原值>1.0(百分比错误)，修正为小数。

## default_spec_values
从 material_name 和 typical_spec 提取: strength_grade, thickness_mm, fire_rating, diameter, grade, dimensions, material, density_kg_m3
不确定给空对象{}

## standard_ref_ids
常见规范: 混凝土GB/T 14902-2012, 钢筋GB 1499.2-2024, 防水GB 18242-2008, 保温GB/T 10801.2-2025, 砂浆GB/T 25181-2019, 涂料GB/T 9755-2014
不确定给空数组[]

## 关键
1. 只返回JSON，不要解释
2. 不确定的留空
3. 禁止编造不存在的标准编号
"""


def load_kb() -> dict:
    with open(KB_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_kb(kb: dict):
    with open(KB_PATH, "w", encoding="utf-8") as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)
        f.write("\n")


def needs_enrichment(m: dict) -> list[str]:
    missing = []
    rule = m.get("quantity_rule") or {}
    if not rule.get("formula", "").strip():
        missing.append("formula")
    if not m.get("default_spec_values"):
        missing.append("default_spec_values")
    lr = m.get("loss_rate_hint")
    if lr is None or (isinstance(lr, (int, float)) and (lr > 1.0 or lr < 0.001)):
        missing.append("loss_rate_hint")
    if not m.get("standard_ref_ids"):
        missing.append("standard_ref_ids")
    return missing


def validate_and_merge(mat: dict, enriched: dict) -> list[str]:
    """校验并合并，返回问题列表"""
    issues = []

    # formula
    rule = enriched.get("quantity_rule") or {}
    formula = rule.get("formula", "")
    if formula:
        tokens = set(re.findall(r'[A-Z_][A-Z_0-9]*', formula))
        known = {"BOQ_AREA", "BOQ_VOLUME", "BOQ_LENGTH", "BOQ_COUNT", "BOQ_WEIGHT",
                 "THICKNESS_MM", "USAGE_PER_M2", "USAGE_PER_M", "COEFF", "LOSS"}
        unknown = tokens - known
        if unknown:
            issues.append(f"未知变量: {unknown}")
        else:
            if not isinstance(mat.get("quantity_rule"), dict):
                mat["quantity_rule"] = {}
            for k in ["formula", "formula_desc", "depends_on", "default_thickness_mm",
                       "default_usage_per_m2", "default_coeff", "unit_conversion", "note"]:
                if k in rule and rule[k] not in (None, ""):
                    if k not in mat["quantity_rule"] or not mat["quantity_rule"][k]:
                        mat["quantity_rule"][k] = rule[k]

    # loss_rate_hint
    lr = enriched.get("loss_rate_hint")
    if lr is not None and isinstance(lr, (int, float)):
        orig = mat.get("loss_rate_hint")
        if orig is None or (isinstance(orig, (int, float)) and (orig > 1.0 or orig < 0.001)):
            if 0.001 <= lr <= 0.5:
                mat["loss_rate_hint"] = lr
            elif lr > 1.0:
                mat["loss_rate_hint"] = lr / 100.0  # 百分比修正

    # default_spec_values
    spec = enriched.get("default_spec_values")
    if spec and isinstance(spec, dict) and len(spec) > 0:
        if not mat.get("default_spec_values"):
            mat["default_spec_values"] = spec

    # standard_ref_ids
    refs = enriched.get("standard_ref_ids")
    if refs and isinstance(refs, list) and len(refs) > 0:
        if not mat.get("standard_ref_ids"):
            mat["standard_ref_ids"] = refs

    # basis
    basis = enriched.get("basis")
    if basis:
        orig = mat.get("basis")
        if not orig or (isinstance(orig, list) and len(orig) == 0):
            if isinstance(basis, str) and basis.strip():
                mat["basis"] = [basis.strip()]
            elif isinstance(basis, list):
                mat["basis"] = basis

    return issues


def build_batch_prompt(batch_systems: list[dict], batch_materials: list[list[dict]]) -> str:
    """为多个系统构建批量补全提示"""
    systems_info = []
    for sys_, mats in zip(batch_systems, batch_materials):
        # 只发送需要补全的材料摘要
        mats_summary = []
        for m in mats:
            mats_summary.append({
                "rec_id": m.get("rec_id"),
                "material_name": m.get("material_name"),
                "role": m.get("role"),
                "unit_hint": m.get("unit_hint"),
                "typical_spec": m.get("typical_spec"),
                "_missing": needs_enrichment(m),
            })
        systems_info.append({
            "system_id": sys_.get("system_id"),
            "system_name": sys_.get("system_name"),
            "phase": sys_.get("phase"),
            "typical_scene": sys_.get("typical_scene"),
            "materials": mats_summary,
        })

    prompt = f"""补全以下{len(batch_systems)}个系统的材料可执行字段。

{json.dumps(systems_info, ensure_ascii=False, indent=2)}

返回格式: {{"systems": [{{"system_id": "...", "materials": [{{"rec_id": "...", "quantity_rule": {{"formula": "..."}}, "loss_rate_hint": 0.0, "default_spec_values": {{}}, "standard_ref_ids": [], "basis": "..."}}]}}]}}

只补全 _missing 中列出的字段。"""
    return prompt


def process_batch(batch: list[tuple[dict, list[dict]]]) -> dict:
    """处理一批系统（10个）"""
    systems = [s for s, _ in batch]
    all_mats = [m for _, ms in batch for m in ms]

    total_todo = sum(1 for m in all_mats if needs_enrichment(m))
    if total_todo == 0:
        return {"enriched": 0, "skipped": len(all_mats)}

    prompt = build_batch_prompt(
        [s for s, _ in batch],
        [[m for m in ms if needs_enrichment(m)] for _, ms in batch]
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    result = {"enriched": 0, "skipped": len(all_mats) - total_todo}

    for attempt in range(3):  # 最多重试2次
        try:
            resp = chat_json(messages, temperature=0.3, max_tokens=16384, thinking=False)
            data = resp.get("json")

            if data and "systems" in data:
                # 构建 rec_id → enriched_data 映射
                enriched_map = {}
                for sys_data in data["systems"]:
                    for m_data in sys_data.get("materials", []):
                        enriched_map[m_data.get("rec_id")] = m_data

                issues = []
                for mat in all_mats:
                    rec_id = mat.get("rec_id", "")
                    if rec_id in enriched_map:
                        mat_issues = validate_and_merge(mat, enriched_map[rec_id])
                        if mat_issues:
                            issues.append({"rec_id": rec_id, "issues": mat_issues})
                        else:
                            result["enriched"] += 1

                if issues:
                    result["issues"] = issues
                result["usage"] = resp.get("usage", {})
                return result

            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            result["error"] = "AI返回无效格式"
            return result

        except Exception as e:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            result["error"] = str(e)[:200]
            return result

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()

    print("=" * 60)
    print("AI批量补全默认值库 (批量版 — 每API调用{N}系统)".format(N=args.batch_size))
    print("=" * 60)

    kb = load_kb()
    systems = kb.get("default_systems", [])
    materials_all = kb.get("material_recommendations", [])

    # 按system_id分组
    from collections import defaultdict
    sys_mat_map = defaultdict(list)
    for m in materials_all:
        sid = m.get("system_id", "unknown")
        sys_mat_map[sid].append(m)

    # 筛选需要补全的系统
    todo_systems = []
    for s in systems:
        sid = s.get("system_id", "")
        mats = sys_mat_map.get(sid, [])
        ai_mats = [m for m in mats if m.get("source_type") == "project_default_kb_ai_candidate"]
        need_mats = [m for m in ai_mats if needs_enrichment(m)]
        if need_mats:
            todo_systems.append((s, need_mats))

    print(f"系统数: {len(systems)}, 需补全系统: {len(todo_systems)}")

    if args.dry_run:
        total_mats = sum(len(ms) for _, ms in todo_systems)
        n_batches = (len(todo_systems) + args.batch_size - 1) // args.batch_size
        print(f"需补全材料: {total_mats}")
        print(f"批次: {n_batches} (每批最多{args.batch_size}系统)")
        print(f"预估时间: {n_batches * 12 / 60:.0f}分钟")
        return 0

    if args.limit > 0:
        todo_systems = todo_systems[:args.limit]

    # 分批
    batches = []
    for i in range(0, len(todo_systems), args.batch_size):
        batches.append(todo_systems[i:i + args.batch_size])

    print(f"共 {len(batches)} 批次")

    # 备份
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f"项目默认值库_batch_backup_{int(time.time())}.json"
    shutil.copy2(KB_PATH, backup_path)
    print(f"已备份: {backup_path}")

    log_fh = open(LOG_PATH, "a", encoding="utf-8")

    total_enriched = 0
    total_errors = 0

    for i, batch in enumerate(batches):
        sys_ids = [s.get("system_id", "?") for s, _ in batch]
        n_mats = sum(len(ms) for _, ms in batch)
        print(f"[{i+1}/{len(batches)}] {len(batch)}系统/{n_mats}材...", end=" ", flush=True)

        result = process_batch(batch)
        enriched = result.get("enriched", 0)
        total_enriched += enriched

        if result.get("error"):
            print(f"ERROR: {result['error'][:100]}")
            total_errors += 1
        elif result.get("issues"):
            print(f"OK +{enriched}材 ({len(result['issues'])}疑)")
        else:
            print(f"OK +{enriched}材")

        result["timestamp"] = time.time()
        result["batch_systems"] = sys_ids
        log_fh.write(json.dumps(result, ensure_ascii=False) + "\n")
        log_fh.flush()

        if i < len(batches) - 1:
            time.sleep(args.delay)

    log_fh.close()
    save_kb(kb)

    new_enriched = sum(1 for m in materials_all
                       if m.get("quantity_rule", {}).get("formula", ""))
    print(f"\n完成! 本次补全: {total_enriched}, 错误: {total_errors}")
    print(f"可执行率: {new_enriched}/{len(materials_all)} ({100*new_enriched/len(materials_all):.1f}%)")
    print(f"日志: {LOG_PATH}")


if __name__ == "__main__":
    main()
