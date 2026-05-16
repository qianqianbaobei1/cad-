#!/usr/bin/env python3
"""用 DeepSeek 为 T3 标准物料库补全：规格模式JSON、标准代号、N5映射、N3参数。

策略：
- 按物料分类分组，每批10条发给AI
- 提供N1全量标准列表作为匹配参考
- 已有规格模式的物料作为few-shot示例
- 只更新确实缺失的字段，保留已有数据
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DATA = ROOT / "标准知识库" / "源数据"

# 权威CSV路径（流水线实际加载的路径）
T3_FJ_PATH = SOURCE_DATA / "01_房屋建筑与装饰工程" / "CSV导出" / "03_t3_标准物料库.csv"
T3_AZ_PATH = SOURCE_DATA / "02_通用安装工程" / "CSV导出" / "02_t3_标准物料库.csv"
N1_PATH = SOURCE_DATA / "03_国家规范库" / "N1_国家规范索引.csv"
N3_PATH = SOURCE_DATA / "03_国家规范库" / "N3_材料技术参数定义.csv"
N5_PATH = SOURCE_DATA / "03_国家规范库" / "N5_材料规范映射.csv"

# 本地副本路径
T3_FJ_LOCAL = ROOT / "标准知识库" / "T3_房建_标准物料库.csv"
T3_AZ_LOCAL = ROOT / "标准知识库" / "T3_安装_标准物料库.csv"

BACKUP_DIR = ROOT / "过程数据" / "分类库备份"
LOG_PATH = ROOT / "过程数据" / "AI补全T3_日志.jsonl"

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


# ── AI 系统提示词 ──
SYSTEM_PROMPT = """你是中国建设工程材料标准化专家，精通国标规范、材料规格参数和采购技术要求。

## 任务
为给定的 T3 标准物料条目补全以下字段：
1. **标准代号** — 与该物料最相关的国家标准编号
2. **规格模式JSON** — 采购时需要明确的规格参数定义
3. **N5映射** — 物料→规范的关系类型和引用条款

## 规格模式JSON 规范
每个规格参数必须包含：
```json
{
  "param": "参数名",
  "type": "enum|text|number",
  "required": true|false,
  "source_standard": "标准编号",
  "source_clause": "条款号",
  "default": "默认值(可选)",
  "unit": "单位(可选)",
  "values": ["允许值1","允许值2"]  // enum类型必填
}
```

常见参数类型和命名：
- 强度等级 (strength_grade) → enum: C20/C25/C30/C35/C40...
- 牌号 (grade) → enum: HRB400/HRB400E/HRB500/HRB500E...
- 厚度 (thickness_mm) → enum/number
- 公称直径 (diameter) → enum: 6/8/10/12/14/16/18/20/22/25/28/32...
- 防火等级 (fire_rating) → enum: A级/B1级/B2级/B3级/甲级/乙级/丙级
- 尺寸 (dimensions) → enum
- 密度 (density_kg_m3) → number/enum
- 材质 (material) → enum: Q235B/Q345B/304不锈钢/316不锈钢...
- 连接方式 (connection_type) → enum
- 表面处理 (surface_treatment) → enum

不要编造不存在的标准编号！不确定的标准引用留空字符串 ""。
不要编造不存在的允许值！只列出真实存在的规格选项。
如果物料本质上没有可变规格参数（如"施工用水"、"素土"等），返回空数组 []。

## 标准代号 选择规范
- 从提供的N1标准列表中选择最直接相关的产品标准
- 优先选 product_standard 类型
- 次选 acceptance_standard 或 construction_standard
- 一个物料可以有多个标准（如产品标准+验收规范），用分号分隔
- 不确定时选最接近的，不要编造

## N5映射 规范
对于物料→规范的关系：
```json
{
  "material_id": "MAT-xxx",
  "standard_id": "GB/T xxxx-20xx",
  "relation_type": "product_standard|acceptance_standard|construction_standard|design_standard",
  "relevance": "primary|secondary",
  "use_scene": "质量验收|进场检验|设计选型|施工控制",
  "covered_params": "覆盖的技术参数",
  "clause_refs": "相关条款",
  "active_flag": "是|否",
  "notes": ""
}
```

## 关键约束
1. 只基于真实存在的国家标准，不确定的不填
2. 规格模式的values必须是真实存在的规格选项
3. 对于无意义规格的材料（水、电、燃料、零星材料等），规格模式返回 []
4. 返回的JSON必须严格符合格式
5. 不为 "调试服务"、"检测服务" 等服务类物料生成材料规格
"""


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def save_csv(path: Path, rows: list[dict]):
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def needs_spec(row: dict) -> bool:
    val = row.get("规格模式JSON", "").strip()
    if not val or val in ("[]", "{}", "null", "None"):
        return True
    try:
        parsed = json.loads(val)
        if isinstance(parsed, list) and len(parsed) == 0:
            return True
        if isinstance(parsed, dict) and len(parsed) == 0:
            return True
        return False
    except json.JSONDecodeError:
        return True


def needs_std_code(row: dict) -> bool:
    return not row.get("标准代号", "").strip()


def load_n1_summary() -> list[dict]:
    """加载N1标准的简要信息供AI匹配"""
    n1_rows = load_csv(N1_PATH)
    summary = []
    for r in n1_rows:
        summary.append({
            "standard_id": r.get("standard_id", ""),
            "standard_name": r.get("standard_name", ""),
            "standard_type": r.get("standard_type", ""),
        })
    return summary


def build_enrichment_prompt(materials: list[dict], n1_summary: list[dict],
                            good_examples: list[dict]) -> str:
    """为一个批次的T3物料构建补全提示"""
    mat_info = []
    for r in materials:
        info = {
            "物料ID": r.get("物料ID", ""),
            "标准名称": r.get("标准名称", ""),
            "分类(一级)": r.get("分类(一级)", ""),
            "分类(二级)": r.get("分类(二级)", ""),
            "分类(三级)": r.get("分类(三级)", ""),
            "采购单位": r.get("采购单位", ""),
            "别名": r.get("别名", ""),
            "特征关键词": r.get("特征关键词", ""),
            "适用范围(附录)": r.get("适用范围(附录)", ""),
            "适用范围(项目)": r.get("适用范围(项目)", ""),
            "品类标准损耗率": r.get("品类标准损耗率", ""),
            "当前标准代号": r.get("标准代号", "") or "(缺失)",
        }
        info["_needs_spec"] = needs_spec(r)
        info["_needs_std_code"] = needs_std_code(r)
        mat_info.append(info)

    prompt = f"""请为以下 {len(materials)} 条 T3 标准物料补全规格模式JSON和标准代号。

## 可用的N1国家标准列表（共{len(n1_summary)}本）
{json.dumps(n1_summary, ensure_ascii=False, indent=2)}

## 已有的高质量规格模式示例（few-shot）
{json.dumps(good_examples, ensure_ascii=False, indent=2)}

## 需要补全的物料
{json.dumps(mat_info, ensure_ascii=False, indent=2)}

## 输出格式
{{
  "materials": [
    {{
      "物料ID": "MAT-xxx",
      "标准代号": "GB/T xxxx-20xx; GB 50xxx-20xx",
      "规格模式JSON": [规格参数数组],
      "N5_entries": [N5映射条目数组]
    }}
  ]
}}

对每条物料：
- 如果 _needs_spec=false，保留原有规格模式JSON，可微调
- 如果 _needs_std_code=false，保留原有标准代号
- 不确定的标准代号留空字符串 ""
- 无意义规格参数的物料返回规格模式JSON: []
- N5_entries 为每条物料的规范映射，不确定时返回空数组 []
"""
    return prompt


def get_good_examples(all_t3: list[dict], n: int = 8) -> list[dict]:
    """选取已有高质量规格模式的物料作为few-shot示例"""
    examples = []
    for r in all_t3:
        if not needs_spec(r) and not needs_std_code(r):
            try:
                spec = json.loads(r["规格模式JSON"])
                if isinstance(spec, list) and len(spec) > 0:
                    examples.append({
                        "物料ID": r.get("物料ID", ""),
                        "标准名称": r.get("标准名称", ""),
                        "分类(一级)": r.get("分类(一级)", ""),
                        "标准代号": r.get("标准代号", ""),
                        "规格模式JSON": spec,
                    })
                    if len(examples) >= n:
                        break
            except json.JSONDecodeError:
                pass

    # 确保示例覆盖不同分类
    seen_cats = set()
    diverse = []
    for ex in examples:
        cat = ex["分类(一级)"]
        if cat not in seen_cats or len(diverse) < n // 2:
            diverse.append(ex)
            seen_cats.add(cat)
    return diverse[:n]


def validate_and_merge(original: dict, enriched: dict) -> list[str]:
    """校验AI返回结果并合并到原始数据，返回警告列表"""
    warnings = []

    # 合并标准代号
    new_std = enriched.get("标准代号", "")
    if new_std and needs_std_code(original):
        # 简单校验
        if any(prefix in new_std for prefix in ["GB", "JGJ", "CECS", "CJJ", "JG", "TB", "DL"]):
            original["标准代号"] = new_std
        else:
            warnings.append(f"标准代号格式可疑: {new_std}")

    # 合并规格模式JSON
    new_spec = enriched.get("规格模式JSON")
    if new_spec and needs_spec(original):
        if isinstance(new_spec, list):
            if len(new_spec) > 0:
                # 校验每个参数结构
                valid = True
                for p in new_spec:
                    if not isinstance(p, dict) or "param" not in p:
                        valid = False
                        break
                if valid:
                    original["规格模式JSON"] = json.dumps(new_spec, ensure_ascii=False)
                else:
                    warnings.append(f"规格模式参数结构无效")
            # 空数组也接受（表示无意义规格参数的材料）
        elif isinstance(new_spec, str):
            try:
                parsed = json.loads(new_spec)
                if isinstance(parsed, list):
                    original["规格模式JSON"] = json.dumps(parsed, ensure_ascii=False)
            except json.JSONDecodeError:
                warnings.append(f"规格模式JSON字符串解析失败")

    return warnings


def process_batch(batch: list[dict], n1_summary: list[dict],
                  good_examples: list[dict]) -> dict:
    """处理一批T3物料"""
    todo = [r for r in batch if needs_spec(r) or needs_std_code(r)]
    if not todo:
        return {"enriched": 0, "skipped": len(batch)}

    prompt = build_enrichment_prompt(todo, n1_summary, good_examples)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    result = {"enriched": 0, "skipped": len(batch) - len(todo)}

    try:
        resp = chat_json(messages, temperature=0.3, max_tokens=8192, thinking=True)
        data = resp.get("json")

        if not data or "materials" not in data:
            result["error"] = f"AI返回无materials字段: {str(resp.get('parse_error', ''))[:200]}"
            return result

        enriched_list = data["materials"]
        enriched_map = {m.get("物料ID"): m for m in enriched_list}

        all_warnings = []
        for r in todo:
            mid = r.get("物料ID", "")
            if mid in enriched_map:
                warnings = validate_and_merge(r, enriched_map[mid])
                if warnings:
                    all_warnings.append({"物料ID": mid, "warnings": warnings})
                result["enriched"] += 1
            else:
                all_warnings.append({"物料ID": mid, "warnings": ["AI未返回"]})

        if all_warnings:
            result["warnings"] = all_warnings

        result["usage"] = resp.get("usage", {})
        return result

    except Exception as e:
        result["error"] = str(e)[:300]
        return result


def main():
    parser = argparse.ArgumentParser(description="AI补全T3物料库")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="限制批次数")
    parser.add_argument("--batch-size", type=int, default=10, help="每批物料数")
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--skip-fj", action="store_true", help="跳过房建")
    parser.add_argument("--skip-az", action="store_true", help="跳过安装")
    args = parser.parse_args()

    print("=" * 60)
    print("AI补全 T3 标准物料库")
    print("=" * 60)

    # 加载数据
    t3_fj = load_csv(T3_FJ_PATH)
    t3_az = load_csv(T3_AZ_PATH)
    n1_summary = load_n1_summary()

    print(f"T3房建: {len(t3_fj)}, T3安装: {len(t3_az)}")
    print(f"N1标准: {len(n1_summary)} 本")

    need_spec_fj = sum(1 for r in t3_fj if needs_spec(r))
    need_spec_az = sum(1 for r in t3_az if needs_spec(r))
    need_std_fj = sum(1 for r in t3_fj if needs_std_code(r))
    need_std_az = sum(1 for r in t3_az if needs_std_code(r))

    print(f"缺规格模式: 房建{need_spec_fj} + 安装{need_spec_az} = {need_spec_fj+need_spec_az}")
    print(f"缺标准代号: 房建{need_std_fj} + 安装{need_std_az} = {need_std_fj+need_std_az}")

    if args.dry_run:
        # 展示需要补全的物料样例
        all_t3 = t3_fj + t3_az
        todo = [r for r in all_t3 if needs_spec(r) or needs_std_code(r)]
        print(f"\n需补全物料: {len(todo)}/{len(all_t3)}")
        cats = defaultdict(int)
        for r in todo:
            cats[r.get("分类(一级)", "其他")] += 1
        print("按分类:")
        for cat, cnt in sorted(cats.items(), key=lambda x: -x[1]):
            print(f"  {cat}: {cnt}")
        return 0

    # 备份
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    for src, label in [(T3_FJ_PATH, "房建"), (T3_AZ_PATH, "安装")]:
        if src.exists():
            dst = BACKUP_DIR / f"T3_{label}_backup_{ts}.csv"
            shutil.copy2(src, dst)
            print(f"已备份: {dst}")

    # 好样例
    all_t3_good = [r for r in t3_fj + t3_az if not needs_spec(r) and not needs_std_code(r)]
    good_examples = get_good_examples(all_t3_good)
    print(f"Few-shot示例: {len(good_examples)}")

    # 处理
    log_fh = open(LOG_PATH, "a", encoding="utf-8")

    batches = []
    if not args.skip_fj:
        for i in range(0, len(t3_fj), args.batch_size):
            batches.append(("房建", t3_fj[i:i + args.batch_size]))

    if not args.skip_az:
        for i in range(0, len(t3_az), args.batch_size):
            batches.append(("安装", t3_az[i:i + args.batch_size]))

    if args.limit > 0:
        batches = batches[:args.limit]

    print(f"共 {len(batches)} 批次")

    total_enriched = 0
    total_skipped = 0
    total_errors = 0

    for i, (label, batch) in enumerate(batches):
        todo = sum(1 for r in batch if needs_spec(r) or needs_std_code(r))
        if todo == 0:
            total_skipped += len(batch)
            continue

        print(f"[{i+1}/{len(batches)}] {label} 批次 ({len(batch)}条, {todo}需补全)...", end=" ", flush=True)
        result = process_batch(batch, n1_summary, good_examples)

        enriched = result.get("enriched", 0)
        skipped = result.get("skipped", 0)
        total_enriched += enriched
        total_skipped += skipped

        if result.get("error"):
            print(f"ERROR: {result['error'][:120]}")
            total_errors += 1
        elif result.get("warnings"):
            print(f"OK (+{enriched}), {len(result['warnings'])} warnings")
        else:
            print(f"OK (+{enriched})")

        result["timestamp"] = time.time()
        result["batch_label"] = label
        log_fh.write(json.dumps(result, ensure_ascii=False) + "\n")
        log_fh.flush()

        if i < len(batches) - 1:
            time.sleep(args.delay)

    log_fh.close()

    # 保存
    save_csv(T3_FJ_PATH, t3_fj)
    save_csv(T3_AZ_PATH, t3_az)
    print(f"\n已保存权威CSV: {T3_FJ_PATH}, {T3_AZ_PATH}")

    # 同步到本地副本
    shutil.copy2(T3_FJ_PATH, T3_FJ_LOCAL)
    shutil.copy2(T3_AZ_PATH, T3_AZ_LOCAL)
    print(f"已同步本地副本: {T3_FJ_LOCAL}, {T3_AZ_LOCAL}")

    print(f"\n{'=' * 60}")
    print(f"完成!")
    print(f"补全: {total_enriched}")
    print(f"跳过: {total_skipped}")
    print(f"错误: {total_errors}")

    # 更新后的统计
    need_spec_fj2 = sum(1 for r in t3_fj if needs_spec(r))
    need_spec_az2 = sum(1 for r in t3_az if needs_spec(r))
    need_std_fj2 = sum(1 for r in t3_fj if needs_std_code(r))
    need_std_az2 = sum(1 for r in t3_az if needs_std_code(r))
    print(f"剩余缺规格模式: {need_spec_fj2+need_spec_az2} (原{need_spec_fj+need_spec_az})")
    print(f"剩余缺标准代号: {need_std_fj2+need_std_az2} (原{need_std_fj+need_std_az})")


if __name__ == "__main__":
    raise SystemExit(main())
