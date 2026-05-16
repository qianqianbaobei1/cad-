#!/usr/bin/env python3
"""用 DeepSeek 批量为项目默认值库的材料推荐补全可执行字段。

补全目标:
  - quantity_rule.formula        算量公式
  - default_spec_values           规格默认值
  - loss_rate_hint                损耗率（修正异常值）
  - standard_ref_ids              N1 标准引用
  - basis                         规范依据（补空）

策略:
  - 按 system 分组，同一系统的材料一起发给 AI（利用上下文）
  - 只处理 AI 候选材料 (source_type=project_default_kb_ai_candidate)
  - 每次 API 调用处理 1 个 system 的材料
  - 多进程并发加速
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
KB_PATH = ROOT / "项目数据" / "项目默认值库.json"
BACKUP_DIR = ROOT / "过程数据" / "分类库备份"
LOG_PATH = ROOT / "过程数据" / "AI补全默认值库_日志.jsonl"

# 把服务端加入 sys.path 以便 import AI客户端
SERVER_DIR = str(ROOT / "服务端")
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

# 加载 .env
env_file = ROOT / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip()

from AI客户端 import chat_json

# ── 系统提示词 ──
SYSTEM_PROMPT = """你是中国建设工程材料专家，精通工程量清单拆解、采购材料算量和国家规范。

你的任务：为给定的材料推荐条目补全可执行字段。每个字段都有严格规范，编造会导致采购错误。

## 算量公式 (quantity_rule.formula) 规范

公式只能使用以下变量：
- BOQ_AREA    — 清单面积(m²)
- BOQ_VOLUME  — 清单体积(m³)
- BOQ_LENGTH  — 清单长度(m)
- BOQ_COUNT   — 清单数量(个/套)
- BOQ_WEIGHT  — 清单重量(kg/t)
- THICKNESS_MM — 厚度(mm)
- USAGE_PER_M2 — 单方用量(kg/m²)
- USAGE_PER_M  — 单米用量(kg/m)
- COEFF       — 综合系数
- LOSS        — 损耗率(小数)

公式必须是数学表达式，运算符只能用 + - * / ( )。
示例：
- BOQ_AREA * THICKNESS_MM / 1000 * (1 + LOSS)       ← 厚度类材料（保温板、混凝土面层）
- BOQ_AREA * USAGE_PER_M2 * (1 + LOSS)               ← 消耗类（粘接剂、涂料、砂浆）
- BOQ_AREA * COEFF * (1 + LOSS)                       ← 面层类（砖、板材、卷材）
- BOQ_VOLUME * COEFF * (1 + LOSS)                     ← 体积类（混凝土、回填）
- BOQ_LENGTH * COEFF * (1 + LOSS)                     ← 线材类（管材、型材、踢脚线）
- BOQ_LENGTH * USAGE_PER_M * (1 + LOSS)               ← 线消耗类（密封胶、止水带）
- BOQ_COUNT * COEFF * (1 + LOSS)                      ← 件数类（门、窗、器具）
- BOQ_WEIGHT * COEFF * (1 + LOSS)                     ← 重量类（钢筋、钢材）

## 规格默认值 (default_spec_values) 规范

从材料名称和 typical_spec 中提取结构化规格参数。
- 参数名用英文小写下划线命名
- 值要带单位（如 "C30", "HRB400E", "50mm", "B1级", "1.5mm厚"）
- 常见参数：strength_grade, thickness_mm, fire_rating, diameter, grade, dimensions, density_kg_m3
- 不要编造值，只从已有信息中提取；无法确定时给空对象 {}

## 损耗率 (loss_rate_hint) 规范
- 必须是小数（0.01 = 1%）
- 只允许范围 0.001 ~ 0.50
- 参考 T3品类标准损耗率:
  混凝土: 0.01, 钢筋: 0.02-0.03, 模板: 0.05, 砌体: 0.02-0.03,
  防水卷材: 0.05-0.08, 保温板: 0.02-0.03, 涂料: 0.03-0.05,
  地砖: 0.03-0.05, 管材: 0.02-0.03, 电缆: 0.03-0.05
- 如果原值 > 1.0 或 < 0.001，说明是异常值，修正为合理值

## 标准引用 (standard_ref_ids) 规范
- 从 N1 国家规范索引中选择最相关的标准
- 格式：["GB/T 14902-2012", "GB 50204-2015"] 等
- 常见材料→规范：
  混凝土 → GB/T 14902-2012, GB 50204-2015
  钢筋 → GB 1499.2-2024, GB 50204-2015
  防水卷材 → GB 18242-2008, GB 50208-2011
  保温板XPS → GB/T 10801.2-2025
  岩棉 → GB/T 25975-2018
  砂浆 → GB/T 25181-2019
  涂料 → GB/T 9755-2014, GB 50210-2018
  钢管 → GB/T 3091-2015
  电线 → GB/T 5023.3-2008
  门窗 → GB/T 8478-2020
  防火门 → GB 12955-2008
- 不确定时写空数组 []

## basis 规范
- 如果原 basis 为空，根据材料类型补充简要规范依据
- 如：["GB/T 14902-2012 预拌混凝土", "GB 50204-2015 混凝土结构工程施工质量验收规范"]

## 关键约束
1. 禁止编造不存在的国家标准编号
2. 损耗率>0.5 或 <0.001 时修正并说明
3. 不确定的字段留空（null/空数组/空对象），不要蒙
4. 只返回 JSON，格式必须与输入完全一致，仅补全缺失字段
"""


def load_kb() -> dict:
    with open(KB_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_kb(kb: dict):
    with open(KB_PATH, "w", encoding="utf-8") as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)
        f.write("\n")


def needs_enrichment(material: dict) -> list[str]:
    """返回该材料需要补全的字段列表"""
    missing = []
    rule = material.get("quantity_rule") or {}
    if not rule.get("formula", "").strip():
        missing.append("quantity_rule.formula")
    if not material.get("default_spec_values"):
        missing.append("default_spec_values")
    lr = material.get("loss_rate_hint")
    if lr is None or (isinstance(lr, (int, float)) and (lr > 0.5 or lr < 0.001)):
        missing.append("loss_rate_hint")
    if not material.get("standard_ref_ids"):
        missing.append("standard_ref_ids")
    basis = material.get("basis")
    if not basis or (isinstance(basis, list) and len(basis) == 0):
        missing.append("basis")
    return missing


def build_enrichment_prompt(system: dict, materials: list[dict]) -> str:
    """为一个系统及其材料构建补全提示"""
    system_info = {
        "system_id": system.get("system_id"),
        "system_name": system.get("system_name"),
        "phase": system.get("phase"),
        "section_code": system.get("section_code"),
        "section_name": system.get("section_name"),
        "applicable_project_category": system.get("applicable_project_category"),
        "applicable_location": system.get("applicable_location"),
        "typical_scene": system.get("typical_scene"),
    }

    materials_info = []
    for m in materials:
        missing = needs_enrichment(m)
        mat = {
            "rec_id": m.get("rec_id"),
            "material_name": m.get("material_name"),
            "role": m.get("role"),
            "typical_spec": m.get("typical_spec"),
            "unit_hint": m.get("unit_hint"),
            "loss_rate_hint": m.get("loss_rate_hint"),
            "basis": m.get("basis"),
            "quantity_rule": m.get("quantity_rule"),
            "default_spec_values": m.get("default_spec_values"),
            "standard_ref_ids": m.get("standard_ref_ids"),
        }
        mat["_missing_fields"] = missing
        materials_info.append(mat)

    prompt = f"""请为以下材料补全可执行字段。

## 系统上下文
{json.dumps(system_info, ensure_ascii=False, indent=2)}

## 需要补全的材料（共{len(materials_info)}条）
{json.dumps(materials_info, ensure_ascii=False, indent=2)}

请对每条材料补全 _missing_fields 中列出的字段。返回格式：
{{"materials": [{{"rec_id": "REC-xxx", 补全的字段...}}]}}

注意：
1. 只补全缺失的字段，已有且正确的字段保持不变
2. 如果原 loss_rate_hint > 1.0 是异常值（可能是百分比），修正为小数
3. 不确定的 standard_ref_ids 写空数组 []
4. 不确定的 default_spec_values 写空对象 {{}}
"""
    return prompt


def validate_enriched(material: dict, enriched: dict) -> list[str]:
    """校验 AI 补全结果，返回问题列表"""
    issues = []

    # 检查 formula
    rule = enriched.get("quantity_rule") or {}
    formula = rule.get("formula", "")
    if formula:
        allowed_vars = {"BOQ_AREA", "BOQ_VOLUME", "BOQ_LENGTH", "BOQ_COUNT", "BOQ_WEIGHT",
                        "THICKNESS_MM", "USAGE_PER_M2", "USAGE_PER_M", "COEFF", "LOSS",
                        "0", "1", "1000", "(", ")", "+", "-", "*", "/", "."}
        # 简单检查：提取所有标识符
        import re
        tokens = set(re.findall(r'[A-Z_][A-Z_0-9]*|\d+\.?\d*', formula))
        unknown = tokens - allowed_vars - {str(i) for i in range(10001)}
        # 数字token不需要检查
        unknown = {t for t in unknown if not t.replace('.','').isdigit()}
        if unknown:
            issues.append(f"公式含未知变量: {unknown}")

    # 检查 loss_rate_hint
    lr = enriched.get("loss_rate_hint")
    if lr is not None and isinstance(lr, (int, float)):
        if lr > 1.0:
            issues.append(f"loss_rate_hint={lr} 异常(>1.0)，可能是百分比")
        elif lr > 0.5:
            issues.append(f"loss_rate_hint={lr} 偏大(>0.5)")

    # 检查 standard_ref_ids
    srefs = enriched.get("standard_ref_ids")
    if srefs and isinstance(srefs, list):
        for ref in srefs:
            if not isinstance(ref, str):
                issues.append(f"standard_ref_ids 含非字符串: {ref}")
            elif not ref.startswith("GB") and not ref.startswith("JGJ") and not ref.startswith("CECS"):
                issues.append(f"标准引用格式可疑: {ref}")

    return issues


def merge_enriched(original: dict, enriched: dict):
    """将 AI 返回的补全字段合入原材料"""
    for field in ["loss_rate_hint", "default_spec_values", "standard_ref_ids", "basis"]:
        if field in enriched and enriched[field] is not None:
            # 不覆盖已有的有效值（除非明确要修正）
            if field == "loss_rate_hint":
                orig = original.get(field)
                if orig is None or (isinstance(orig, (int, float)) and (orig > 0.5 or orig < 0.001)):
                    original[field] = enriched[field]
            elif field == "standard_ref_ids":
                if not original.get(field) and enriched.get(field):
                    original[field] = enriched[field]
            elif field == "basis":
                orig = original.get(field)
                if not orig or (isinstance(orig, list) and len(orig) == 0):
                    original[field] = enriched[field]
            elif field == "default_spec_values":
                if not original.get(field) and enriched.get(field):
                    original[field] = enriched[field]

    # quantity_rule 特殊处理
    orig_rule = original.get("quantity_rule") or {}
    enrich_rule = enriched.get("quantity_rule") or {}
    if enrich_rule:
        if not isinstance(orig_rule, dict):
            orig_rule = {}
        for k in ["formula", "formula_desc", "depends_on", "default_thickness_mm",
                   "default_usage_per_m2", "default_coeff", "unit_conversion", "note"]:
            if k in enrich_rule and enrich_rule[k] is not None and enrich_rule[k] != "":
                if k not in orig_rule or not orig_rule[k]:
                    orig_rule[k] = enrich_rule[k]
        original["quantity_rule"] = orig_rule


def process_system(system: dict, materials: list[dict], dry_run: bool = False) -> dict:
    """处理单个系统的材料补全"""
    # 只处理需要补全的材料
    todo = [(m, needs_enrichment(m)) for m in materials]
    todo = [(m, missing) for m, missing in todo if missing]

    if not todo:
        return {"system_id": system.get("system_id"), "enriched": 0, "skipped": len(materials)}

    if dry_run:
        return {
            "system_id": system.get("system_id"),
            "enriched": 0,
            "skipped": len(materials),
            "would_enrich": len(todo),
            "sample_missing": {m.get("rec_id"): missing for m, missing in todo[:3]},
        }

    prompt = build_enrichment_prompt(system, [m for m, _ in todo])
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    result = {"system_id": system.get("system_id"), "enriched": 0, "skipped": len(materials) - len(todo)}

    try:
        resp = chat_json(messages, temperature=0.3, max_tokens=8192, thinking=True)
        data = resp.get("json")

        if not data or "materials" not in data:
            result["error"] = f"AI返回无materials字段: {str(resp.get('parse_error', ''))[:200]}"
            return result

        enriched_materials = data["materials"]
        enriched_map = {m.get("rec_id"): m for m in enriched_materials}

        issues = []
        for mat, missing in todo:
            rec_id = mat.get("rec_id")
            if rec_id in enriched_map:
                enriched = enriched_map[rec_id]
                mat_issues = validate_enriched(mat, enriched)
                if mat_issues:
                    issues.append({"rec_id": rec_id, "issues": mat_issues})
                else:
                    merge_enriched(mat, enriched)
                    result["enriched"] += 1
            else:
                issues.append({"rec_id": rec_id, "issues": ["AI未返回此材料"]})

        if issues:
            result["issues"] = issues

        result["usage"] = resp.get("usage", {})
        return result

    except Exception as e:
        result["error"] = str(e)[:300]
        return result


def main():
    parser = argparse.ArgumentParser(description="AI批量补全默认值库可执行字段")
    parser.add_argument("--dry-run", action="store_true", help="只分析不实际调用AI")
    parser.add_argument("--limit", type=int, default=0, help="只处理前N个系统（0=全部）")
    parser.add_argument("--workers", type=int, default=3, help="并发数（默认3）")
    parser.add_argument("--delay", type=float, default=0.5, help="API调用间隔秒数")
    parser.add_argument("--resume", type=str, default="", help="从指定system_id恢复")
    args = parser.parse_args()

    print("=" * 60)
    print("AI批量补全默认值库可执行字段")
    print("=" * 60)

    # 加载
    kb = load_kb()
    systems = kb.get("default_systems", [])
    materials_all = kb.get("material_recommendations", [])

    print(f"系统数: {len(systems)}")
    print(f"材料推荐数: {len(materials_all)}")

    # 按 system_id 分组材料
    sys_materials: dict[str, list[dict]] = defaultdict(list)
    for m in materials_all:
        sid = m.get("system_id", "unknown")
        sys_materials[sid].append(m)

    # 统计需要补全的
    total_need = 0
    for m in materials_all:
        if m.get("source_type") == "project_default_kb_ai_candidate":
            if needs_enrichment(m):
                total_need += 1

    print(f"需要补全的材料: {total_need}/{len(materials_all)}")

    if args.dry_run:
        print("\n=== 干运行模式 ===")
        systems_need = 0
        for sys_ in systems:
            sid = sys_.get("system_id", "")
            mats = sys_materials.get(sid, [])
            todo = [(m, needs_enrichment(m)) for m in mats
                    if m.get("source_type") == "project_default_kb_ai_candidate"]
            todo = [(m, missing) for m, missing in todo if missing]
            if todo:
                systems_need += 1
                if systems_need <= 10:
                    print(f"\n{sid} ({sys_.get('system_name','')}): {len(todo)}材需补全")
                    for m, missing in todo[:3]:
                        print(f"  {m.get('rec_id')} {m.get('material_name')}: 缺{missing}")
        print(f"\n共 {systems_need} 个系统需要补全")
        return 0

    # 处理系统
    todo_systems = []
    resume_found = not args.resume
    for sys_ in systems:
        sid = sys_.get("system_id", "")
        if not resume_found:
            if sid == args.resume:
                resume_found = True
            else:
                continue
        mats = sys_materials.get(sid, [])
        ai_mats = [m for m in mats if m.get("source_type") == "project_default_kb_ai_candidate"]
        todo = [(m, needs_enrichment(m)) for m in ai_mats]
        todo_need = [(m, missing) for m, missing in todo if missing]
        if todo_need:
            todo_systems.append((sys_, [m for m, _ in todo_need]))

    print(f"实际处理系统数: {len(todo_systems)}")

    if args.limit > 0:
        todo_systems = todo_systems[:args.limit]
        print(f"限制到前 {args.limit} 个")

    # 备份原文件
    backup_path = BACKUP_DIR / f"项目默认值库_backup_{int(time.time())}.json"
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)
    print(f"已备份到: {backup_path}")

    # 日志文件
    log_fh = open(LOG_PATH, "a", encoding="utf-8")

    # 并发处理
    enriched_total = 0
    skipped_total = 0
    errors_total = 0
    issues_total = 0

    start_time = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {}
        for i, (sys_, mats) in enumerate(todo_systems):
            # 间隔提交，避免瞬间大量请求
            if i > 0:
                time.sleep(args.delay)
            future = executor.submit(process_system, sys_, mats, False)
            futures[future] = sys_

        for future in as_completed(futures):
            sys_ = futures[future]
            try:
                result = future.result()
            except Exception as e:
                print(f"ERROR {sys_.get('system_id')}: {e}")
                errors_total += 1
                continue

            sid = result.get("system_id", "?")
            enriched = result.get("enriched", 0)
            skipped = result.get("skipped", 0)
            enriched_total += enriched
            skipped_total += skipped

            if result.get("error"):
                print(f"ERROR [{sid}]: {result['error'][:120]}")
                errors_total += 1
            elif result.get("issues"):
                print(f"WARN  [{sid}]: +{enriched}材, {len(result['issues'])}疑问")
                issues_total += len(result["issues"])
            else:
                print(f"OK    [{sid}]: +{enriched}材")

            # 写日志
            result["timestamp"] = time.time()
            log_fh.write(json.dumps(result, ensure_ascii=False) + "\n")
            log_fh.flush()

    elapsed = time.time() - start_time

    # 保存
    save_kb(kb)
    log_fh.close()

    print(f"\n{'=' * 60}")
    print(f"完成! 耗时 {elapsed:.0f}s")
    print(f"补全材料: {enriched_total}")
    print(f"跳过(无需补全): {skipped_total}")
    print(f"错误: {errors_total}")
    print(f"有疑问需人工复核: {issues_total}")
    if enriched_total > 0:
        print(f"已保存到: {KB_PATH}")
    print(f"日志: {LOG_PATH}")
    print(f"备份: {backup_path}")


if __name__ == "__main__":
    raise SystemExit(main())
