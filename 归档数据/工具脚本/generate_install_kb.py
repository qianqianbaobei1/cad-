#!/usr/bin/env python3
"""
安装工程知识库自动生成脚本。

功能：
- 从 Q0 安装工程清单范围读取所有附录和分部
- 按分部拆分批次（降低 JSON 截断风险）
- 每批独立保存到 过程数据/AI候选_安装/
- checkpoint.json 支持断点续传
- 注入 T3 物料库已有知识，提升生成准确性
- 复用现有 验证AI候选数据.py 和 合并AI候选到默认值库.py

用法：
  # 查看所有批次计划
  python3 工具脚本/generate_install_kb.py --dry-run

  # 只生成通风空调工程
  python3 工具脚本/generate_install_kb.py --appendix 通风空调工程 --execute

  # 断点续传
  python3 工具脚本/generate_install_kb.py --execute --resume

  # 生成全部安装工程（长时间运行）
  python3 工具脚本/generate_install_kb.py --execute --resume
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "工具脚本"))
from kb_gen_config import (
    OUTPUT_DIR, CHECKPOINT_PATH, Q0_INSTALL_CSV, T3_INSTALL_CSV,
    APPENDIX_PRIORITY, BATCH_DEFAULTS, P0_APPENDIXES,
    STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_COMPLETED, STATUS_FAILED, STATUS_SKIPPED,
    load_env, get_api_config, load_checkpoint, save_checkpoint,
    batch_key, is_batch_completed, mark_batch, batch_output_path,
    load_t3_install_context, load_q0_install_rows,
    load_q2_context_for_appendix, format_q2_prompt_block,
)

# ── 项目类型树、部位列表（注入 prompt 约束 AI 输出结构） ──
PROJECT_BUSINESS_TREE = [
    {"category": "居住建筑", "subcategories": {
        "住宅": ["普通住宅", "洋房", "高层住宅", "超高层住宅"],
        "政府保障房": ["安置房", "公租房", "廉租房", "经适房"],
        "公寓": ["人才公寓", "酒店式公寓", "学生公寓"],
        "别墅": ["独栋别墅", "联排别墅", "叠拼别墅"],
        "地下车库": ["住宅配套地库", "独立地库", "人防工程"],
        "相关配套": ["物业用房", "会所", "配电房", "垃圾站", "配套商业"],
        "更新改造": ["老旧小区改造", "功能提升", "外立面整治"]}},
    {"category": "办公建筑", "subcategories": {
        "综合办公楼": ["甲级写字楼", "企业总部", "研发办公"],
        "政务大厅": ["政务中心", "办事大厅", "市民中心"],
        "司法建筑": ["法院", "检察院", "公安局办公楼", "派出所"]}},
    {"category": "宾馆酒店", "subcategories": {"宾馆酒店": ["星级酒店", "快捷酒店", "招待所", "度假酒店"]}},
    {"category": "商业建筑", "subcategories": {"商业建筑": ["购物商场", "超市", "商业街", "批发市场"]}},
    {"category": "卫生建筑", "subcategories": {"卫生建筑": ["综合医院", "专科医院", "社区卫生中心", "养老院/疗养院"]}},
    {"category": "教育建筑", "subcategories": {"教育建筑": ["幼儿园", "小学", "中学", "中等专业学校", "高等院校", "科研楼", "特殊教育学校"]}},
    {"category": "交通建筑", "subcategories": {"交通建筑": ["客运站", "火车站", "机场航站楼", "地铁站（地上建筑）", "码头建筑"]}},
    {"category": "文体建筑", "subcategories": {"文体建筑": ["图书馆", "博物馆", "展览馆", "文化馆", "体育馆", "剧院", "电影院", "档案馆"]}},
    {"category": "工业建筑", "subcategories": {"工业建筑": ["通用厂房", "仓库", "特种厂房（洁净/恒温/重型）", "物流中心"]}},
    {"category": "配套设施", "subcategories": {"配套设施": ["变配电房", "泵房", "锅炉房", "垃圾站", "雨水处理站"]}},
]

LOCATIONS = [
    "基础", "地下室", "地下车库", "人防区", "屋面", "室外平台", "设备层",
    "外墙", "内墙", "楼地面", "天棚", "厨卫", "阳台", "门窗洞口",
    "管井", "强电井", "弱电井", "设备间", "消防控制室",
    "水泵房", "配电房", "机房", "室外道路", "室外管网",
]


def load_external_material_reference(appendix_name: str) -> list[str]:
    """加载外部物料参考数据（来自中铁中建行业数据），返回材料名列表。"""
    ref_path = ROOT / "标准知识库" / "安装工程_外部物料参考_compact.json"
    if not ref_path.exists():
        return []
    try:
        with open(ref_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get(appendix_name, [])
    except Exception:
        return []


def build_system_prompt(appendix_name: str, section_code: str) -> str:
    """构建带 T3 知识注入和外部物料参考的系统提示词。"""
    t3_entries = load_t3_install_context(appendix_name)
    ext_materials = load_external_material_reference(appendix_name)

    ref_blocks = []

    # T3 物料库参考
    if t3_entries:
        samples = t3_entries[:20]
        lines = ["\n## 已有 T3 安装标准物料（仅供参照，不得直接复制到不相关分部）"]
        for e in samples:
            lines.append(
                f"- {e['material_name']} | {e['category']} | "
                f"单位:{e['unit']} | 标准:{e['standard_code']} | 损耗:{e['loss_rate']}"
            )
        ref_blocks.append("\n".join(lines))

    # 外部行业物料参考（来自中铁中建实际采购数据）
    if ext_materials:
        names = ext_materials[:100]
        ref_blocks.append(
            "\n## 行业真实采购物料名参考（中铁/中建/云筑实际数据，可用于校验材料名真实性）\n"
            + "、".join(names)
        )

    ref_context = "\n".join(ref_blocks)

    # Q2 定额材料约束
    q2_context = load_q2_context_for_appendix(appendix_name)
    q2_block = format_q2_prompt_block(q2_context)

    return f"""你是中国工程量清单、安装材料、国家规范和采购拆解专家。
任务：基于输入的国家清单分部范围、项目类型树和已有物料知识，生成"{appendix_name}"下分部"{section_code}"的默认材料体系候选。

硬约束：
1. 分部工程必须以输入的国家清单 Q0 分部为准，不得自造分部名称。
2. 项目业务树只用于判断项目类型，不替代国家清单分部。
3. 默认推荐不是最终采购结论，所有默认规格都必须标记为需复核。
4. 不得编造不存在的国家标准（GB/GB/T/JGJ/CECS 等），不确定时写入 review_notes 标注"待核实"。
5. 安装工程必须区分电气、给排水、消防、通风空调、智能化，不得混用材料。
6. 所有材料名称应符合 T3 标准物料命名规范；无法确定时保留描述性名称并标记 confidence=low。
7. 安装辅材（减振器、支架、螺栓、垫片、密封胶等）必须关联到对应主材设备。
8. 只能返回 JSON，不得返回 JSON 之外的解释文字。
{ref_context}
{q2_block}

输出 JSON Schema：
{{
  "batch_id": "string",
  "trade": "通用安装工程",
  "appendix_name": "string",
  "systems": [
    {{
      "system_id": "SYS-XXXX-XXXXXX-XXX",
      "trade": "通用安装工程",
      "appendix_name": "string",
      "section_code": "string",
      "section_name": "string",
      "system_name": "string（描述性名称，不超过20字）",
      "applicable_project_category": ["string"],
      "applicable_location": ["string"],
      "typical_scene": "string",
      "confidence": "medium",
      "must_yield_to_feature_text": true,
      "must_pass_t3": true,
      "can_auto_add_materials": false,
      "requires_review_if_not_in_feature": true,
      "review_notes": ["string"]
    }}
  ],
  "material_recommendations": [
    {{
      "rec_id": "REC-XXXX-XXX",
      "system_id": "SYS-XXXX-XXXXXX-XXX",
      "material_name": "string（T3标准名称优先）",
      "role": "主材|辅材|周转材料|措施材料",
      "typical_spec": "string（保守规格描述）",
      "spec_params": {{}},
      "unit_hint": "string（台/套/m/m²/m³/kg/t/个/项）",
      "auxiliary_group": [],
      "basis": ["string（真实国标编号，不确定则不填）"],
      "loss_rate_hint": 0.0,
      "quantity_rule_hint": "string",
      "required_params": [],
      "source_type": "project_default_kb_ai_candidate",
      "confidence": "medium",
      "can_auto_add": false,
      "can_auto_pass_when_default_used": false,
      "requires_review_if_not_in_feature": true,
      "review_notes": ["string"]
    }}
  ],
  "conflict_rules": []
}}

生成要求：
1. 每个系统必须绑定一个输入中的 section_code + section_name。
2. 每个分部至少1个系统，最多5个系统（按设备类型或安装方式细分）。
3. 每个系统至少3条材料推荐，最多15条。
4. 材料角色只能是：主材、辅材、周转材料、措施材料。
5. 大型设备本体（如空调室外机、水泵、变压器等）必须作为主材，规格标注"按设计选型"。
6. 辅材必须包含安装必需的消耗品（减振器、螺栓、垫片、密封胶、焊条等）。
7. 所有默认参数均需复核，can_auto_add 和 can_auto_pass_when_default_used 必须为 false。"""


def build_batches(appendix_filter: str = "", section_filter: str = "") -> list[dict]:
    """从 Q0 CSV 读取安装工程清单行，按附录+分部构建批次。"""
    all_rows = load_q0_install_rows()
    if not all_rows:
        print("错误：无法读取 Q0 安装工程清单。", file=sys.stderr)
        return []

    # 按 (appendix_name, section_code) 分组
    grouped: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "section_name": "", "sample_items": [], "item_count": 0,
    })

    for row in all_rows:
        appendix = row["appendix_name"]
        sec_code = row["section_code"]
        if not appendix or not sec_code:
            continue
        if appendix_filter and appendix_filter not in appendix:
            continue
        if section_filter and sec_code != section_filter:
            continue

        key = (appendix, sec_code)
        grp = grouped[key]
        if not grp["section_name"]:
            grp["section_name"] = row["section_name"]
        grp["item_count"] += 1
        if len(grp["sample_items"]) < 8:
            grp["sample_items"].append({
                "item_code": row["item_code"],
                "item_name": row["item_name"],
                "features": row["features"],
                "unit": row["unit"],
                "work_content": row["work_content"],
            })

    # 构建批次列表，按优先级排序
    batches = []
    idx = 1
    sorted_keys = sorted(
        grouped.keys(),
        key=lambda k: (APPENDIX_PRIORITY.get(k[0], {}).get("priority", 99), k[0], k[1])
    )
    for appendix, sec_code in sorted_keys:
        grp = grouped[(appendix, sec_code)]
        batches.append({
            "batch_id": f"BATCH-{idx:03d}",
            "target_trade": "通用安装工程",
            "target_appendix": appendix,
            "target_section": {
                "section_code": sec_code,
                "section_name": grp["section_name"],
                "sample_items": grp["sample_items"],
                "item_count": grp["item_count"],
            },
        })
        idx += 1
    return batches


def sample_existing_defaults(limit: int = 12) -> tuple[list[dict], list[dict]]:
    """读取现有默认库作为 style reference。"""
    from kb_gen_config import EXISTING_DEFAULTS
    if not EXISTING_DEFAULTS.exists():
        return [], []
    kb = json.loads(EXISTING_DEFAULTS.read_text(encoding="utf-8"))
    systems = [
        {
            "system_id": s.get("system_id", ""),
            "system_name": s.get("system_name", ""),
            "phase": s.get("phase", ""),
            "applicable_project_category": s.get("applicable_project_category", []),
            "applicable_location": s.get("applicable_location", []),
        }
        for s in kb.get("default_systems", [])
        if "安装" in s.get("trade", "")
    ][:limit]
    recs = [
        {
            "rec_id": r.get("rec_id", ""),
            "material_name": r.get("material_name", ""),
            "role": r.get("role", ""),
            "typical_spec": r.get("typical_spec", ""),
            "basis": r.get("basis", []),
        }
        for r in kb.get("material_recommendations", [])
        if r.get("source_type") == "project_default_kb"
    ][:limit]
    return systems, recs


def build_payload(batch: dict) -> dict:
    """为单个批次构建 API 请求 payload。"""
    existing_systems, existing_recs = sample_existing_defaults()
    appendix = batch["target_appendix"]
    sec = batch["target_section"]
    return {
        "task": "generate_default_material_system_candidates",
        "scope": {
            "trade": "通用安装工程",
            "appendix_name": appendix,
            "target_section": sec,
            "project_business_tree": PROJECT_BUSINESS_TREE,
            "locations": LOCATIONS,
            "existing_systems_sample": existing_systems,
            "existing_recommendations_sample": existing_recs,
        },
        "generation_batch": {
            "batch_id": batch["batch_id"],
            "target_appendix": appendix,
            "target_section_code": sec["section_code"],
        },
    }


# ── JSON 修复工具（从原脚本移植） ──

def _repair_truncated_json(content: str) -> any:
    """尝试修复截断/损坏的 JSON。"""
    stack: list[str] = []
    in_string = False
    escape = False
    last_valid_pos = 0
    for i, ch in enumerate(content):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch == "]":
            if stack and stack[-1] == "[":
                stack.pop()
                if not stack:
                    last_valid_pos = i + 1
        elif ch == "}":
            if stack and stack[-1] == "{":
                stack.pop()
                if not stack:
                    last_valid_pos = i + 1

    if last_valid_pos > 0 and last_valid_pos < len(content):
        try:
            return json.loads(content[:last_valid_pos])
        except json.JSONDecodeError:
            pass

    if not stack:
        return None
    closing = "".join("]" if c == "[" else "}" for c in reversed(stack))
    try:
        return json.loads(content + closing)
    except json.JSONDecodeError:
        pass

    if stack:
        last_comma = max(content.rfind(",", 0, len(content) - 50),
                         content.rfind(",", 0, len(content)))
        if last_comma > 0:
            try:
                return json.loads(content[:last_comma + 1] + closing)
            except json.JSONDecodeError:
                pass
    return None


def extract_json(text: str) -> any:
    """从 AI 返回文本中提取 JSON。"""
    content = text.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?", "", content).strip()
        content = re.sub(r"```$", "", content).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        repaired = _repair_truncated_json(content)
        if repaired is not None:
            return repaired
        match = re.search(r"\{[\s\S]*\}", content)
        if match:
            return json.loads(match.group())
        raise


def call_ai(system_prompt: str, user_content: str, api_cfg: dict) -> dict:
    """调用 AI API，返回 {content, json, parse_error, finish_reason, usage}。"""
    from openai import OpenAI
    import httpx

    http_client = httpx.Client(timeout=api_cfg.get("request_timeout", 300), proxy=None)
    client = OpenAI(
        api_key=api_cfg["api_key"],
        base_url=api_cfg["base_url"],
        http_client=http_client,
    )

    response = client.chat.completions.create(
        model=api_cfg["model"],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=api_cfg.get("temperature", 0.2),
        max_tokens=api_cfg.get("max_tokens", 16000),
        stream=False,
    )

    content = response.choices[0].message.content or ""
    finish = response.choices[0].finish_reason or ""
    usage = {
        "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
        "completion_tokens": response.usage.completion_tokens if response.usage else 0,
        "total_tokens": response.usage.total_tokens if response.usage else 0,
    }

    parse_error = ""
    parsed = None
    try:
        parsed = extract_json(content)
    except Exception as e:
        parse_error = str(e)

    return {
        "content": content,
        "json": parsed,
        "parse_error": parse_error,
        "finish_reason": finish,
        "usage": usage,
    }


# ── 验证与规范化 ──

def normalize_candidate(candidate: dict, batch: dict) -> dict:
    """对 AI 输出做安全后处理。"""
    if not isinstance(candidate, dict):
        raise ValueError(f"候选数据不是 dict: {type(candidate)}")

    candidate.setdefault("batch_id", batch["batch_id"])
    candidate.setdefault("trade", "通用安装工程")
    candidate.setdefault("appendix_name", batch["target_appendix"])
    candidate.setdefault("systems", [])
    candidate.setdefault("material_recommendations", [])
    candidate.setdefault("conflict_rules", [])

    for sys_entry in candidate.get("systems", []):
        sys_entry["trade"] = "通用安装工程"
        sys_entry["appendix_name"] = batch["target_appendix"]
        if sys_entry.get("confidence") == "high":
            sys_entry["confidence"] = "medium"
        sys_entry["must_yield_to_feature_text"] = True
        sys_entry["must_pass_t3"] = True
        sys_entry["can_auto_add_materials"] = False
        sys_entry["requires_review_if_not_in_feature"] = True
        sys_entry.setdefault("review_notes", [])

    system_ids = {s.get("system_id", "") for s in candidate.get("systems", [])}
    for rec in candidate.get("material_recommendations", []):
        if rec.get("confidence") == "high":
            rec["confidence"] = "medium"
        rec["source_type"] = "project_default_kb_ai_candidate"
        rec["can_auto_add"] = False
        rec["can_auto_pass_when_default_used"] = False
        rec["requires_review_if_not_in_feature"] = True
        rec.setdefault("spec_params", {})
        rec.setdefault("auxiliary_group", [])
        rec.setdefault("basis", [])
        rec.setdefault("required_params", [])
        rec.setdefault("review_notes", [])
        if rec.get("system_id") not in system_ids:
            rec["review_notes"].append("system_id 未匹配任何已声明系统，需核实")

    return candidate


def validate_candidate(candidate: dict) -> list[str]:
    """对 AI 生成的候选做硬约束校验。"""
    issues = []
    systems = candidate.get("systems", [])
    recs = candidate.get("material_recommendations", [])

    seen_sys_ids = set()
    for s in systems:
        sid = s.get("system_id", "")
        if not sid:
            issues.append("存在空 system_id 的系统")
        elif sid in seen_sys_ids:
            issues.append(f"重复 system_id: {sid}")
        seen_sys_ids.add(sid)
        if s.get("confidence") == "high":
            issues.append(f"系统 {sid} confidence 为 high，应降为 medium")
        if s.get("can_auto_add_materials") is True:
            issues.append(f"系统 {sid} can_auto_add_materials 应为 false")
        if not s.get("section_code"):
            issues.append(f"系统 {sid} 缺少 section_code")

    for i, r in enumerate(recs):
        sid = r.get("system_id", "")
        if sid and sid not in seen_sys_ids:
            issues.append(f"材料推荐[{i}] {r.get('material_name','')} 的 system_id={sid} 不在系统中")
        if r.get("confidence") == "high":
            issues.append(f"材料推荐[{i}] {r.get('material_name','')} confidence 为 high")
        if r.get("can_auto_add") is True:
            issues.append(f"材料推荐[{i}] {r.get('material_name','')} can_auto_add 应为 false")
        role = r.get("role", "")
        if role not in ("主材", "辅材", "周转材料", "措施材料"):
            issues.append(f"材料推荐[{i}] {r.get('material_name','')} role={role} 不合法")
        basis = r.get("basis", [])
        for b in basis:
            if b and not re.match(r"^(GB|GB/T|GB/Z|JGJ|CECS|CJJ|CJ|JB|HG|SH|SY|DL|NB|YB|QB)", str(b)):
                issues.append(f"材料推荐[{i}] {r.get('material_name','')} 标准号格式可疑: {b}")

    return issues


def process_batch(batch: dict, api_cfg: dict, checkpoint: dict, args) -> dict:
    """处理单个批次：调用 AI → 校验 → 保存 → 更新 checkpoint。
    返回带 'status' 的结果字典。"""
    appendix = batch["target_appendix"]
    sec_code = batch["target_section"]["section_code"]
    sec_name = batch["target_section"]["section_name"]
    batch_id = batch["batch_id"]
    output_path = batch_output_path(appendix, sec_code)

    print(f"\n{'='*60}")
    print(f"[{batch_id}] {appendix} / {sec_code} {sec_name}")
    print(f"  示例清单项: {batch['target_section']['item_count']} 条")
    print(f"{'='*60}")

    # 标记 in_progress
    mark_batch(checkpoint, appendix, sec_code, STATUS_IN_PROGRESS)
    save_checkpoint(checkpoint)

    system_prompt = build_system_prompt(appendix, sec_code)
    payload = build_payload(batch)
    user_content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    started = time.time()
    row = {}
    last_error = ""
    max_attempts = args.retries + 1

    for attempt in range(1, max_attempts + 1):
        try:
            print(f"  [Attempt {attempt}/{max_attempts}] 调用 {api_cfg['model']}...", flush=True)
            result = call_ai(system_prompt, user_content, api_cfg)

            if result.get("parse_error"):
                raw_path = OUTPUT_DIR / f"{batch_id}_{appendix.replace(' ','_')}_{sec_code}_raw.txt"
                raw_path.write_text(result.get("content", ""), encoding="utf-8")
                raise ValueError(f"JSON解析失败: {result['parse_error']} | raw保存至 {raw_path}")

            candidate = normalize_candidate(result["json"], batch)
            issues = validate_candidate(candidate)

            elapsed = round(time.time() - started, 2)
            row = {
                "batch": batch,
                "elapsed_sec": elapsed,
                "attempts": attempt,
                "usage": result.get("usage", {}),
                "validation_issues": issues,
                "candidate": candidate,
            }

            # 保存独立批次文件
            output_path.write_text(
                json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            # 更新 checkpoint
            mark_batch(checkpoint, appendix, sec_code, STATUS_COMPLETED,
                       output_file=str(output_path),
                       elapsed_sec=elapsed,
                       systems_count=len(candidate.get("systems", [])),
                       recs_count=len(candidate.get("material_recommendations", [])),
                       validation_issues_count=len(issues))
            save_checkpoint(checkpoint)

            print(f"  ✓ 完成 | 耗时={elapsed}s | 系统{len(candidate.get('systems',[]))}个 "
                  f"材料{len(candidate.get('material_recommendations',[]))}条 "
                  f"校验问题{len(issues)}条")
            for issue in issues[:5]:
                print(f"    ⚠ {issue}")
            if len(issues) > 5:
                print(f"    ... 还有{len(issues)-5}条")
            row["status"] = "success"
            return row

        except Exception as exc:
            last_error = traceback.format_exc()
            if attempt < max_attempts:
                print(f"  ✗ Attempt {attempt} 失败: {exc}", flush=True)
                print(f"  ⏳ {args.retry_sleep}s 后重试...", flush=True)
                time.sleep(args.retry_sleep)
                continue

            elapsed = round(time.time() - started, 2)
            row = {
                "batch": batch,
                "elapsed_sec": elapsed,
                "attempts": attempt,
                "error": str(exc),
                "traceback": last_error,
            }
            output_path.write_text(
                json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            mark_batch(checkpoint, appendix, sec_code, STATUS_FAILED,
                       output_file=str(output_path),
                       error=str(exc)[:500])
            save_checkpoint(checkpoint)
            print(f"  ✗ 失败 | {exc}", flush=True)
            row["status"] = "failed"
            return row


def main() -> int:
    parser = argparse.ArgumentParser(description="安装工程知识库自动生成")
    parser.add_argument("--execute", action="store_true", help="真实调用 API。不加则只输出批次计划。")
    parser.add_argument("--dry-run", action="store_true", help="输出批次计划后退出。")
    parser.add_argument("--appendix", default="", help="只处理包含该文本的附录，如 通风空调工程。")
    parser.add_argument("--section-code", default="", help="只处理指定分部编码，如 030701。")
    parser.add_argument("--batch-limit", type=int, default=0, help="最多处理多少个批次，0=不限制。")
    parser.add_argument("--resume", action="store_true", help="跳过 checkpoint 中已完成的批次。")
    parser.add_argument("--force", action="store_true", help="即使已完成也重新生成。")
    parser.add_argument("--model", default="", help="覆盖 .env 中的模型名。")
    parser.add_argument("--temperature", type=float, default=BATCH_DEFAULTS["temperature"])
    parser.add_argument("--max-tokens", type=int, default=BATCH_DEFAULTS["max_tokens"])
    parser.add_argument("--retries", type=int, default=BATCH_DEFAULTS["retries"])
    parser.add_argument("--retry-sleep", type=float, default=BATCH_DEFAULTS["retry_sleep"])
    parser.add_argument("--request-timeout", type=float, default=BATCH_DEFAULTS["request_timeout"])
    args = parser.parse_args()

    # 加载 API 配置
    api_cfg = get_api_config()
    if args.model:
        api_cfg["model"] = args.model
    api_cfg["temperature"] = args.temperature
    api_cfg["max_tokens"] = args.max_tokens
    api_cfg["retries"] = args.retries
    api_cfg["retry_sleep"] = args.retry_sleep
    api_cfg["request_timeout"] = args.request_timeout

    if not api_cfg.get("api_key"):
        print("错误：未找到 API Key。请在 .env 中设置 DEEPSEEK_API_KEY 或 MOONSHOT_API_KEY。", file=sys.stderr)
        return 1

    print(f"API: model={api_cfg['model']} | base_url={api_cfg['base_url']}")

    # 构建批次
    batches = build_batches(args.appendix, args.section_code)
    if not batches:
        print("没有匹配的批次。请检查 --appendix / --section-code 参数。")
        return 1

    # 断点过滤
    checkpoint = load_checkpoint()
    skipped = 0
    if args.resume and not args.force:
        active = []
        for b in batches:
            appendix = b["target_appendix"]
            sec_code = b["target_section"]["section_code"]
            if is_batch_completed(checkpoint, appendix, sec_code):
                skipped += 1
                mark_batch(checkpoint, appendix, sec_code, STATUS_SKIPPED,
                           note="restart skipped")
            else:
                active.append(b)
        batches = active
        if skipped:
            save_checkpoint(checkpoint)

    if args.batch_limit > 0:
        batches = batches[:args.batch_limit]

    # 打印计划
    plan = {
        "mode": "execute" if args.execute else "dry_run",
        "total_batches": len(batches),
        "skipped_by_resume": skipped,
        "batches": [
            {
                "batch_id": b["batch_id"],
                "appendix": b["target_appendix"],
                "section_code": b["target_section"]["section_code"],
                "section_name": b["target_section"]["section_name"],
                "item_count": b["target_section"]["item_count"],
                "priority": APPENDIX_PRIORITY.get(b["target_appendix"], {}).get("priority", 99),
            }
            for b in batches
        ],
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)

    if args.dry_run or not args.execute:
        print("\n不加 --execute 不会调用 API。加 --execute 开始生成。")
        return 0

    if not batches:
        print("所有批次已完成，无需处理。")
        return 0

    # 执行
    success = 0
    failed = 0
    for batch in batches:
        result = process_batch(batch, api_cfg, checkpoint, args)
        if result.get("status") == "success":
            success += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"完成: {success} 成功, {failed} 失败, {skipped} 跳过")
    print(f"输出目录: {OUTPUT_DIR}")
    print(f"断点文件: {CHECKPOINT_PATH}")
    print(f"{'='*60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
