#!/usr/bin/env python3
"""Use DeepSeek to generate draft default material systems for FJ/AZ scopes.

This script is intentionally conservative:
- It only reads the local Q0 scope for 房屋建筑与装饰工程 and 通用安装工程.
- It writes AI output to draft files.
- It never overwrites 项目默认值库.json.
- It requires DEEPSEEK_API_KEY in the environment for real execution.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
Q0_FJ = ROOT / "项目数据" / "本地知识库包" / "sources" / "01_定额库" / "Q0_清单项目编码_房建工程.json"
Q0_AZ = ROOT / "项目数据" / "本地知识库包" / "sources" / "01_定额库" / "Q0_清单项目编码_安装工程.json"
DEFAULT_KB = ROOT / "项目数据" / "项目默认值库.json"
PROMPT_PATH = ROOT / "工具脚本" / "DeepSeek_房建安装默认材料体系提示词.md"
DEFAULT_OUTPUT = ROOT / "项目数据" / "AI候选_房建安装默认材料体系.json"
DEFAULT_JSONL = ROOT / "项目数据" / "AI候选_房建安装默认材料体系.jsonl"
DEFAULT_RAW_DIR = ROOT / "项目数据" / "AI候选_raw"

DEFAULT_PROMPT = """
你是中国工程量清单、建筑材料、安装材料、国家规范和采购拆解专家。
任务：基于输入的国家清单范围、项目业务类型、房间/部位和现有默认库，生成“房屋建筑与装饰工程”和“通用安装工程”的默认材料体系候选。

硬约束：
1. 分部工程必须以国家清单 Q0 的工程范围为准，不得自造分部名称。
2. 项目业务树只用于判断项目类型，不替代国家清单分部。
3. 默认推荐不是最终采购结论，所有默认规格都必须标记为需复核。
4. 不得编造不存在的国家标准；不确定时写入 review_notes。
5. 安装工程必须区分电气、给排水、消防、通风空调、智能化，不得混用材料。
6. 房建装饰必须区分屋面、地下室、厨卫、外墙、内墙、楼地面、天棚、门窗洞口。
7. 推荐材料必须优先使用中国建设工程定额库(Q1/Q2)中真实存在的材料名，不得编造定额中不存在的材料。
8. 所有材料名称应符合 T3 标准物料命名规范；无法确定时保留描述性名称并标记 confidence=low。
9. 安装辅材（减振器、支架、螺栓、垫片、密封胶等）必须关联到对应主材设备。
10. 只能返回 JSON，不得返回 JSON 之外的解释文字。

输出 JSON Schema：
{
  "batch_id": "string",
  "trade": "房屋建筑与装饰工程|通用安装工程",
  "appendix_name": "string",
  "systems": [
    {
      "system_id": "SYS-...",
      "trade": "string",
      "appendix_name": "string",
      "section_code": "string",
      "section_name": "string",
      "system_name": "string",
      "applicable_project_category": ["string"],
      "applicable_location": ["string"],
      "typical_scene": "string",
      "confidence": "medium",
      "must_yield_to_feature_text": true,
      "must_pass_t3": true,
      "can_auto_add_materials": false,
      "requires_review_if_not_in_feature": true,
      "review_notes": []
    }
  ],
  "material_recommendations": [
    {
      "rec_id": "REC-...",
      "system_id": "SYS-...",
      "material_name": "string",
      "role": "主材|辅材|周转材料|措施材料",
      "typical_spec": "string",
      "spec_params": {},
      "unit_hint": "string",
      "auxiliary_group": [],
      "basis": [],
      "loss_rate_hint": 0.0,
      "quantity_rule_hint": "string",
      "required_params": [],
      "source_type": "project_default_kb_ai_candidate",
      "confidence": "medium",
      "can_auto_add": false,
      "can_auto_pass_when_default_used": false,
      "requires_review_if_not_in_feature": true,
      "review_notes": []
    }
  ],
  "conflict_rules": []
}

生成要求：
1. 每个系统必须绑定一个国家清单 section_code + section_name。
2. 每个材料必须说明 role，只能使用：主材、辅材、周转材料、措施材料。
3. 默认规格必须保守，只能作为常规候选。
4. 如果某个清单范围通常不应自动推材料，只输出系统并在 review_notes 中说明原因。
5. 所有默认参数均需复核，can_auto_add 和 can_auto_pass_when_default_used 必须为 false。
""".strip()


PROJECT_BUSINESS_TREE = [
    {
        "category": "居住建筑",
        "subcategories": {
            "住宅": ["普通住宅", "洋房", "高层住宅", "超高层住宅"],
            "政府保障房": ["安置房", "公租房", "廉租房", "经适房"],
            "公寓": ["人才公寓", "酒店式公寓", "学生公寓"],
            "别墅": ["独栋别墅", "联排别墅", "叠拼别墅"],
            "地下车库": ["住宅配套地库", "独立地库", "人防工程"],
            "相关配套": ["物业用房", "会所", "配电房", "垃圾站", "配套商业"],
            "更新改造": ["老旧小区改造", "功能提升", "外立面整治"],
        },
    },
    {
        "category": "办公建筑",
        "subcategories": {
            "综合办公楼": ["甲级写字楼", "企业总部", "研发办公"],
            "政务大厅": ["政务中心", "办事大厅", "市民中心"],
            "司法建筑": ["法院", "检察院", "公安局办公楼", "派出所"],
            "办公建筑其他": [],
        },
    },
    {"category": "宾馆酒店", "subcategories": {"宾馆酒店": ["星级酒店", "快捷酒店", "招待所", "度假酒店"]}},
    {"category": "商业建筑", "subcategories": {"商业建筑": ["购物商场", "超市", "商业街", "批发市场"]}},
    {"category": "卫生建筑", "subcategories": {"卫生建筑": ["综合医院", "专科医院", "社区卫生中心", "养老院/疗养院"]}},
    {
        "category": "教育建筑",
        "subcategories": {"教育建筑": ["幼儿园", "小学", "中学", "中等专业学校", "高等院校", "科研楼", "特殊教育学校"]},
    },
    {"category": "交通建筑", "subcategories": {"交通建筑": ["客运站", "火车站", "机场航站楼", "地铁站（地上建筑）", "码头建筑"]}},
    {
        "category": "文体建筑",
        "subcategories": {"文体建筑": ["图书馆", "博物馆", "展览馆", "文化馆", "体育馆", "剧院", "电影院", "档案馆"]},
    },
    {"category": "工业建筑", "subcategories": {"工业建筑": ["通用厂房", "仓库", "特种厂房（洁净/恒温/重型）", "物流中心"]}},
    {"category": "配套设施", "subcategories": {"配套设施": ["变配电房", "泵房", "锅炉房", "垃圾站", "雨水处理站"]}},
    {
        "category": "专业分包",
        "subcategories": {"专业分包": ["精装修工程", "幕墙工程", "弱电智能化", "消防工程", "电梯工程", "钢结构工程", "桩基工程", "土石方工程"]},
    },
]


LOCATIONS = [
    "基础",
    "地下室",
    "地下车库",
    "人防区",
    "屋面",
    "外墙",
    "内墙",
    "楼地面",
    "天棚",
    "厨卫",
    "阳台",
    "门窗洞口",
    "管井",
    "强电井",
    "弱电井",
    "设备间",
    "消防控制室",
    "水泵房",
    "配电房",
    "机房",
    "室外道路",
    "室外管网",
]


TARGET_APPENDIX_ORDER = [
    "屋面及防水工程",
    "保温、隔热、防腐工程",
    "门 窗 工 程",
    "楼地面装饰工程",
    "墙、柱面装饰与隔断、幕墙工程",
    "天 棚 工 程",
    "油漆、涂料、裱糊工程",
    "电气设备安装工程",
    "给排水、采暖、燃气工程",
    "消防工程",
    "通风空调工程",
    "建筑智能化工程",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def q0_rows(path: Path) -> list[dict]:
    data = read_json(path)
    return data.get("rows", data if isinstance(data, list) else [])


def compact_row(row: dict) -> dict:
    return {
        "trade": row.get("专业工程名称", ""),
        "appendix_name": row.get("附录名称", ""),
        "section_code": row.get("分部编码", ""),
        "section_name": row.get("分部名称", ""),
        "item_code": row.get("项目编码", ""),
        "item_name": row.get("项目名称", ""),
        "features": row.get("项目特征", ""),
        "unit": row.get("计量单位", ""),
        "work_content": row.get("工作内容", ""),
    }


def load_gb_scope() -> list[dict]:
    rows = [compact_row(r) for r in q0_rows(Q0_FJ)] + [compact_row(r) for r in q0_rows(Q0_AZ)]
    rows = [r for r in rows if r["trade"] in {"房屋建筑与装饰工程", "通用安装工程"}]
    return rows


def build_batches(
    rows: list[dict],
    appendix_filter: str = "",
    section_code_filter: str = "",
    per_section: bool = False,
) -> list[dict]:
    grouped: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    for row in rows:
        appendix = row["appendix_name"]
        if appendix_filter and appendix_filter not in appendix:
            continue
        if section_code_filter and row["section_code"] != section_code_filter:
            continue
        key = (row["trade"], appendix)
        if row["section_code"] not in grouped[key]:
            grouped[key][row["section_code"]] = {
                "section_code": row["section_code"],
                "section_name": row["section_name"],
                "sample_items": [],
            }
        if len(grouped[key][row["section_code"]]["sample_items"]) < 6:
            grouped[key][row["section_code"]]["sample_items"].append(
                {
                    "item_code": row["item_code"],
                    "item_name": row["item_name"],
                    "features": row["features"],
                    "unit": row["unit"],
                    "work_content": row["work_content"],
                }
            )

    def sort_key(item: tuple[tuple[str, str], dict[str, dict]]) -> tuple[int, str, str]:
        (trade, appendix), _sections = item
        try:
            idx = TARGET_APPENDIX_ORDER.index(appendix)
        except ValueError:
            idx = 999
        return idx, trade, appendix

    batches = []
    idx = 1
    for (trade, appendix), sections in (item for item in sorted(grouped.items(), key=sort_key)):
        if per_section:
            for section in sections.values():
                batches.append(
                    {
                        "batch_id": f"BATCH-{idx:03d}",
                        "target_trade": trade,
                        "target_appendix": appendix,
                        "target_sections": [section],
                        "target_project_categories": PROJECT_BUSINESS_TREE,
                    }
                )
                idx += 1
            continue
        batches.append(
            {
                "batch_id": f"BATCH-{idx:03d}",
                "target_trade": trade,
                "target_appendix": appendix,
                "target_sections": list(sections.values()),
                "target_project_categories": PROJECT_BUSINESS_TREE,
            }
        )
        idx += 1
    return batches


def sample_existing_defaults(limit: int = 16) -> tuple[list[dict], list[dict]]:
    if not DEFAULT_KB.exists():
        return [], []
    kb = read_json(DEFAULT_KB)
    systems = [
        {
            "system_id": s.get("system_id", ""),
            "phase": s.get("phase", ""),
            "system_name": s.get("system_name", ""),
            "applicable_project_category": s.get("applicable_project_category", []),
            "applicable_location": s.get("applicable_location", []),
        }
        for s in kb.get("default_systems", [])[:limit]
    ]
    recs = [
        {
            "rec_id": r.get("rec_id", ""),
            "system_id": r.get("system_id", ""),
            "material_name": r.get("material_name", ""),
            "role": r.get("role", ""),
            "typical_spec": r.get("typical_spec", ""),
            "basis": r.get("basis", []),
        }
        for r in kb.get("material_recommendations", [])[:limit]
    ]
    return systems, recs


def build_payload(batch: dict, scope_rows: list[dict]) -> dict:
    existing_systems, existing_recs = sample_existing_defaults()
    section_codes = {s.get("section_code", "") for s in batch.get("target_sections", []) if s.get("section_code")}
    rows_for_appendix = [
        r for r in scope_rows
        if r["trade"] == batch["target_trade"] and r["appendix_name"] == batch["target_appendix"]
        and (not section_codes or r["section_code"] in section_codes)
    ][:80]
    return {
        "task": "generate_default_material_system_candidates",
        "scope": {
            "trades": ["房屋建筑与装饰工程", "通用安装工程"],
            "gb_scope_rows": rows_for_appendix,
            "project_business_tree": PROJECT_BUSINESS_TREE,
            "locations": LOCATIONS,
            "existing_systems_sample": existing_systems,
            "existing_recommendations_sample": existing_recs,
        },
        "generation_batch": batch,
    }


def _repair_truncated_json(content: str) -> Any:
    """Try to salvage valid portion of truncated/invalid JSON."""
    # Try to find the outermost JSON object and repair it
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
        # Found a complete outermost object/array earlier than end
        candidate = content[:last_valid_pos]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Try closing any unclosed structures
    if not stack:
        return None
    closing = "".join("]" if c == "[" else "}" for c in reversed(stack))
    repaired = content + closing
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Try without the last incomplete element (most common for truncated lists)
    # Find last comma before the truncation point and truncate there
    if stack:
        last_comma = max(content.rfind(",", 0, len(content) - 50), content.rfind(",", 0, len(content)))
        if last_comma > 0:
            try:
                candidate = content[:last_comma + 1] + closing
                # Close the array/object that was being built
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass
                # Try with a placeholder terminator
                bare = content[:last_comma] + closing
                try:
                    return json.loads(bare)
                except json.JSONDecodeError:
                    pass
            except Exception:
                pass

    return None


def extract_json(text: str) -> Any:
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


def call_deepseek(messages: list[dict], model: str, temperature: float, max_tokens: int, timeout: float) -> dict:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY。请先在环境变量中设置，不要把 Key 写入脚本。")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    try:
        from openai import OpenAI
        import httpx
    except ImportError as exc:
        raise RuntimeError("缺少 openai/httpx 包，请先安装项目依赖。") from exc

    http_client = httpx.Client(proxy=None, timeout=timeout)
    client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,
    )
    choice = response.choices[0]
    content = choice.message.content or ""
    parsed, parse_error = None, ""
    try:
        parsed = extract_json(content)
    except Exception as exc:
        parse_error = str(exc)
    return {
        "content": content,
        "json": parsed,
        "parse_error": parse_error,
        "finish_reason": choice.finish_reason,
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
            "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            "total_tokens": response.usage.total_tokens if response.usage else 0,
        },
    }


def validate_candidate(candidate: dict, valid_sections: set[tuple[str, str, str]]) -> list[str]:
    issues: list[str] = []
    systems = candidate.get("systems") or []
    recs = candidate.get("material_recommendations") or []
    system_ids = {s.get("system_id") for s in systems if s.get("system_id")}
    for system in systems:
        key = (system.get("trade", ""), system.get("appendix_name", ""), system.get("section_code", ""))
        if key not in valid_sections:
            issues.append(f"system {system.get('system_id')}: section not in Q0 {key}")
        if system.get("confidence") == "high":
            issues.append(f"system {system.get('system_id')}: default confidence cannot be high")
        if system.get("can_auto_add_materials") is not False:
            issues.append(f"system {system.get('system_id')}: can_auto_add_materials must be false")
    for rec in recs:
        if rec.get("system_id") not in system_ids:
            issues.append(f"rec {rec.get('rec_id')}: broken system_id {rec.get('system_id')}")
        if rec.get("confidence") == "high":
            issues.append(f"rec {rec.get('rec_id')}: default confidence cannot be high")
        if rec.get("can_auto_add") is not False:
            issues.append(f"rec {rec.get('rec_id')}: can_auto_add must be false")
        if rec.get("can_auto_pass_when_default_used") is not False:
            issues.append(f"rec {rec.get('rec_id')}: can_auto_pass_when_default_used must be false")
    return issues


def normalize_review_notes(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(x) for x in value if x]
    return [str(value)]


def normalize_candidate(candidate: dict, batch: dict) -> dict:
    """Fill deterministic fields and enforce safe defaults before validation."""
    if not isinstance(candidate, dict):
        raise ValueError("DeepSeek 返回内容不是 JSON object。")

    trade = candidate.get("trade") or batch.get("target_trade", "")
    appendix = candidate.get("appendix_name") or batch.get("target_appendix", "")
    candidate["trade"] = trade
    candidate["appendix_name"] = appendix
    candidate.setdefault("batch_id", batch.get("batch_id", ""))

    systems = candidate.get("systems")
    if not isinstance(systems, list):
        systems = []
        candidate["systems"] = systems
    for system in systems:
        if not isinstance(system, dict):
            continue
        system["trade"] = system.get("trade") or trade
        system["appendix_name"] = system.get("appendix_name") or appendix
        if system.get("confidence") == "high" or not system.get("confidence"):
            system["confidence"] = "medium"
        system["must_yield_to_feature_text"] = True
        system["must_pass_t3"] = True
        system["can_auto_add_materials"] = False
        system["requires_review_if_not_in_feature"] = True
        system["review_notes"] = normalize_review_notes(system.get("review_notes"))

    recs = candidate.get("material_recommendations")
    if not isinstance(recs, list):
        recs = []
        candidate["material_recommendations"] = recs
    for rec in recs:
        if not isinstance(rec, dict):
            continue
        if rec.get("confidence") == "high" or not rec.get("confidence"):
            rec["confidence"] = "medium"
        rec["source_type"] = "project_default_kb_ai_candidate"
        rec["can_auto_add"] = False
        rec["can_auto_pass_when_default_used"] = False
        rec["requires_review_if_not_in_feature"] = True
        rec["review_notes"] = normalize_review_notes(rec.get("review_notes"))
        rec.setdefault("required_params", [])
        rec.setdefault("spec_params", {})
        rec.setdefault("auxiliary_group", [])
        rec.setdefault("basis", [])

    conflict_rules = candidate.get("conflict_rules")
    if not isinstance(conflict_rules, list):
        candidate["conflict_rules"] = []
    return candidate


def append_jsonl(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def batch_section_key(batch: dict) -> tuple[str, str, str]:
    sections = batch.get("target_sections", []) or []
    section_code = ""
    if len(sections) == 1:
        section_code = sections[0].get("section_code", "")
    return (batch.get("target_trade", ""), batch.get("target_appendix", ""), section_code)


def successful_keys_from_jsonl(path: Path) -> set[tuple[str, str, str]]:
    if not path.exists():
        return set()
    keys: set[tuple[str, str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("error") or row.get("validation_issues"):
            continue
        batch = row.get("batch") or {}
        keys.add(batch_section_key(batch))
    return keys


def write_raw_response(raw_dir: Path, batch: dict, content: str) -> str:
    raw_dir.mkdir(parents=True, exist_ok=True)
    safe_appendix = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", batch.get("target_appendix", "unknown"))
    section = ""
    sections = batch.get("target_sections", []) or []
    if len(sections) == 1:
        section = "_" + re.sub(r"[^\w.-]+", "_", sections[0].get("section_code", ""))
    path = raw_dir / f"{batch.get('batch_id', 'BATCH')}_{safe_appendix}{section}_raw.txt"
    path.write_text(content or "", encoding="utf-8")
    return str(path)


def main() -> int:
    load_env(ROOT / ".env")
    parser = argparse.ArgumentParser(description="DeepSeek 生成房建/安装默认材料体系候选。")
    parser.add_argument("--execute", action="store_true", help="真实调用 DeepSeek API。未设置时只输出批次计划。")
    parser.add_argument("--batch-limit", type=int, default=0, help="本次最多执行多少个 appendix 批次，0=不限制。")
    parser.add_argument("--appendix", default="", help="只处理包含该文本的国标附录/工程范围。")
    parser.add_argument("--section-code", default="", help="只处理指定国标分部编码，例如 010902。")
    parser.add_argument("--per-section", action="store_true", help="按分部编码拆小批次生成，能显著降低坏 JSON 概率。")
    parser.add_argument("--model", default=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=16000)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--resume", action="store_true", help="跳过 JSONL 中已成功且无校验问题的分部批次。")
    parser.add_argument("--retries", type=int, default=3, help="每个批次失败后的重试次数。")
    parser.add_argument("--retry-sleep", type=float, default=5.0, help="每次重试前等待秒数。")
    parser.add_argument("--request-timeout", type=float, default=180.0, help="单次 DeepSeek 请求超时时间。")
    args = parser.parse_args()

    scope_rows = load_gb_scope()
    batches = build_batches(scope_rows, args.appendix, args.section_code, args.per_section)
    if args.resume:
        done_keys = successful_keys_from_jsonl(args.jsonl)
        before = len(batches)
        batches = [b for b in batches if batch_section_key(b) not in done_keys]
        skipped = before - len(batches)
    else:
        skipped = 0
    if args.batch_limit > 0:
        batches = batches[: args.batch_limit]

    valid_sections = {
        (r["trade"], r["appendix_name"], r["section_code"])
        for r in scope_rows
        if r.get("section_code")
    }

    prompt_text = DEFAULT_PROMPT
    plan = {
        "mode": "execute" if args.execute else "dry_run",
        "scope_row_count": len(scope_rows),
        "batch_count": len(batches),
        "skipped_by_resume": skipped,
        "batches": [
            {
                "batch_id": b["batch_id"],
                "target_trade": b["target_trade"],
                "target_appendix": b["target_appendix"],
                "section_count": len(b["target_sections"]),
            }
            for b in batches
        ],
    }
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)

    all_candidates = {
        "meta": {
            "generated_by": "deepseek_生成房建安装默认材料体系.py",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "model": args.model,
            "status": "draft_not_merged",
            "note": "AI 候选草稿，未覆盖 项目默认值库.json。",
        },
        "batches": [],
    }

    for batch in batches:
        payload = build_payload(batch, scope_rows)
        messages = [
            {
                "role": "system",
                "content": prompt_text + "\n\n请严格返回 JSON，不要输出 JSON 之外的内容。",
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            },
        ]
        started = time.time()
        row = {}
        last_error = ""
        for attempt in range(1, max(args.retries, 0) + 2):
            try:
                result = call_deepseek(messages, args.model, args.temperature, args.max_tokens, args.request_timeout)
                if result.get("parse_error"):
                    raw_path = write_raw_response(args.raw_dir, batch, result.get("content", ""))
                    raise ValueError(f"{result['parse_error']} | raw_response={raw_path}")
                candidate = normalize_candidate(result["json"], batch)
                issues = validate_candidate(candidate, valid_sections)
                row = {
                    "batch": batch,
                    "elapsed_sec": round(time.time() - started, 2),
                    "attempts": attempt,
                    "usage": result.get("usage", {}),
                    "validation_issues": issues,
                    "candidate": candidate,
                }
                break
            except Exception as exc:
                last_error = repr(exc)
                if attempt <= args.retries:
                    print(
                        f"{batch['batch_id']} {batch['target_appendix']} attempt {attempt} failed: {last_error}; retrying...",
                        flush=True,
                    )
                    time.sleep(args.retry_sleep)
                    continue
                row = {
                    "batch": batch,
                    "elapsed_sec": round(time.time() - started, 2),
                    "attempts": attempt,
                    "error": last_error,
                }
        append_jsonl(args.jsonl, row)
        all_candidates["batches"].append(row)
        if row.get("error"):
            print(f"{batch['batch_id']} {batch['target_appendix']} failed: {row['error']}", flush=True)
        else:
            print(f"{batch['batch_id']} {batch['target_appendix']} done | validation_issues={len(row.get('validation_issues', []))}", flush=True)

    write_json(args.output, all_candidates)
    print(f"draft written: {args.output}")
    print(f"jsonl written: {args.jsonl}")
    ok = sum(1 for row in all_candidates["batches"] if not row.get("error") and not row.get("validation_issues"))
    failed = sum(1 for row in all_candidates["batches"] if row.get("error"))
    issue_batches = sum(1 for row in all_candidates["batches"] if row.get("validation_issues"))
    print(f"summary: success={ok}, failed={failed}, validation_issue_batches={issue_batches}, skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
