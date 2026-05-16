"""
拆解引擎 v5 — AI推理 + 知识库Grounding 混合架构（含规格提取）

流程:
  输入清单
    ↓
  Step 0 [输入解析]: 编码/名称/特征/显式规格提取
    ↓
  Step 1 [AI推理]: 清单项 → 施工工序链（KB:T1做grounding）
    ↓
  Step 2 [AI推理]: 工序 → 材料品种推导（KB:T2规则注入prompt，强调规格）
    ↓
  Step 2.5 [规格补齐]: 拆分+输入注入+T3规格模式匹配+必填检查
    ↓
  Step 3 [确定性计算]: 消耗量计算 + 损耗率 + 系数（不用AI）
    ↓
  Step 4 [KB校验]: T3标准化 + 品类树损耗率 + N3/N4/N6规范
    ↓
  Step 5 [AI评估]: 可信度 + 漏项提示 + 规格完整性审核
    ↓
  输出采购物料清单
"""
import csv
import json
import os
import re
import sys
import uuid
from pathlib import Path

from 配置 import KB_01, KB_FJ, KB_AZ, KB_NORM, KB_CAT

# 省份适配
from 省份适配 import (
    load_province_context, match_material_rules, match_param_override, match_t5_dimension,
    ProvinceContext,
)

ROOT = Path(__file__).resolve().parents[1]


def read_csv(path: Path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _parse_json_field(val):
    if not val or val.strip() in ("", "[]", "null"):
        return []
    try:
        parsed = json.loads(val)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    return [x.strip().strip('"').strip("'") for x in re.split(r"[;,，、]", val.strip("[]")) if x.strip()]


# ══════════════════════════════════════
# 规格提取规则 — 从材料名中拆分规格参数
# ══════════════════════════════════════

def _spec_extraction_rules() -> list[dict]:
    """规格提取正则规则。每条规则定义从文本中提取何种规格参数。

    与 T3 参数映射表（工具脚本/T3_参数映射表.json，79个参数）配套使用。
    此处仅覆盖可从材料名称中用正则直接拆出的参数。
    """
    return [
        # ── 混凝土相关 ──
        {"pattern": r"C\d{2,3}(?!\d)", "field": "strength_grade", "desc": "混凝土强度等级"},
        {"pattern": r"P\d{1,2}(?!\d)", "field": "impermeability_grade", "desc": "抗渗等级"},
        {"pattern": r"F\d{2,3}(?!\d)", "field": "frost_resistance_grade", "desc": "抗冻等级"},
        # ── 钢筋/钢材相关 ──
        {"pattern": r"HRB\d+E?|HPB\d+|CRB\d+", "field": "steel_grade", "desc": "钢筋等级"},
        {"pattern": r"Q\d{3}[A-Z]?", "field": "steel_grade_q", "desc": "结构钢牌号"},
        # ── 管材/直径相关 ──
        {"pattern": r"DN\d+(?:\.\d+)?", "field": "dn_size", "desc": "公称直径"},
        {"pattern": r"[Φφ]\d+(?:\.\d+)?(?!\s*[×xX])", "field": "diameter", "desc": "直径(mm)"},
        {"pattern": r"外径\s*[Φφ]?\d+(?:\.\d+)?", "field": "outer_diameter", "desc": "外径"},
        # ── 尺寸/厚度相关 ──
        {"pattern": r"\d+(?:\.\d+)?\s*[×xX]\s*\d+(?:\.\d+)?(?:\s*[×xX]\s*\d+(?:\.\d+)?)?\s*mm", "field": "dimensions", "desc": "长×宽×高尺寸"},
        {"pattern": r"\d+(?:\.\d+)?\s*mm\s*厚", "field": "thickness", "desc": "厚度(mm)"},
        {"pattern": r"[δΔ]\s*\d+(?:\.\d+)?", "field": "thickness_delta", "desc": "壁厚/板厚"},
        # ── 防火/性能等级 ──
        {"pattern": r"[AB]\s*[12]级", "field": "fire_rating", "desc": "防火/燃烧等级"},
        {"pattern": r"\d+(?:\.\d+)?级", "field": "performance_grade", "desc": "性能等级（如10.9级螺栓、4.8级）"},
        # ── 配合比/标号 ──
        {"pattern": r"\d+:\d+(?:\.\d+)?(?::\d+(?:\.\d+)?)?", "field": "mix_ratio", "desc": "配合比"},
        {"pattern": r"M\d{1,2}\.?\d?", "field": "mortar_grade", "desc": "砂浆强度等级"},
        # ── 密度/容重 ──
        {"pattern": r"\d+(?:\.\d+)?\s*kg/m³", "field": "density", "desc": "密度/容重"},
    ]


def _parse_spec_pattern(spec_json_str: str) -> list[dict]:
    """解析 T3 物料库中的 规格模式JSON 字段。"""
    if not spec_json_str or spec_json_str.strip() in ("", "[]", "null"):
        return []
    try:
        parsed = json.loads(spec_json_str)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _split_spec_from_name(name: str, existing_spec: str = "") -> tuple[str, str, list[dict]]:
    """把混入材料名的规格参数拆到 spec_hint。返回 (core_name, spec_hint, extracted_specs)。

    例如: "C30预拌混凝土" → ("预拌混凝土", "C30", [{"value":"C30", "field":"strength_grade"}])
          "热轧带肋钢筋 HRB400E" → ("热轧带肋钢筋", "HRB400E", [{"value":"HRB400E", "field":"steel_grade"}])
    """
    original = str(name or "").strip()
    work = original
    extracted = []
    seen_values = set()

    for rule in _spec_extraction_rules():
        pattern = rule.get("pattern", "")
        try:
            matches = list(re.finditer(pattern, work, re.I))
        except re.error:
            continue
        for m in matches:
            value = m.group(0).strip()
            if not value or value in seen_values:
                continue
            seen_values.add(value)
            extracted.append({
                "value": value,
                "field": rule.get("field", ""),
                "desc": rule.get("desc", ""),
            })
        try:
            work = re.sub(pattern, " ", work, flags=re.I)
        except re.error:
            pass

    core = re.sub(r"\s+", " ", work).strip(" -_/，,;；:：()（）[]【】")
    core = re.sub(r"^(级|厚|型|规格|型号)\s*", "", core).strip()
    core = re.sub(r"\s+(级|厚|型)$", "", core).strip()

    # 合并已有规格和从名称中拆出的规格
    spec_values = []
    if existing_spec:
        spec_values.extend([x.strip() for x in re.split(r"[;；,，]", str(existing_spec)) if x.strip()])
    spec_values.extend([s["value"] for s in extracted])
    dedup_specs = list(dict.fromkeys(spec_values))

    return core or original, "；".join(dedup_specs), extracted


# ══════════════════════════════════════
# T3 规格索引 — 物料ID → 规格模式
# ══════════════════════════════════════

_t3_spec_index = None

def _load_t3_spec_index(force_reload: bool = False) -> dict:
    """加载 T3 物料规格模式索引。返回 {material_id: spec_patterns_list}。"""
    global _t3_spec_index
    if _t3_spec_index is not None and not force_reload:
        return _t3_spec_index
    _t3_spec_index = {}
    t3_all = load_t3()
    for r in t3_all:
        mid = r.get("物料ID", "").strip()
        if not mid:
            continue
        patterns = _parse_spec_pattern(r.get("规格模式JSON", ""))
        if patterns:
            _t3_spec_index[mid] = {
                "standard_name": r.get("标准名称", "").strip(),
                "spec_patterns": patterns,
                "standard_code": r.get("标准代号", "").strip(),
                "unit": r.get("采购单位", "").strip(),
                "category_path": r.get("品类展示路径", "").strip(),
            }
    return _t3_spec_index


_param_mapping = None

def load_param_mapping(force_reload: bool = False) -> dict:
    """加载 T3 参数映射表（79个参数名→类型/单位/默认值/提取规则）。"""
    global _param_mapping
    if _param_mapping is not None and not force_reload:
        return _param_mapping
    mapping_path = ROOT / "工具脚本" / "T3_参数映射表.json"
    if mapping_path.exists():
        with open(mapping_path, "r", encoding="utf-8") as f:
            _param_mapping = json.load(f)
    else:
        _param_mapping = {}
    return _param_mapping


# ══════════════════════════════════════
# 显式规格提取 — 从输入文本中提取用户已声明的规格
# ══════════════════════════════════════

def _extract_explicit_specs(features: dict, feature_text: str) -> dict[str, str]:
    """从项目特征和原文中提取用户显式声明的规格参数。

    返回 {规格字段: 值}，例如 {"strength_grade": "C30", "steel_grade": "HRB400"}
    这些值在后续补齐中有最高优先级——因为用户明确写了。
    """
    specs = {}
    text = f"{feature_text} {' '.join(f'{k}:{v}' for k,v in features.items())}"

    # 从 features 字典中取已解析的
    strength = features.get("混凝土强度等级", "")
    if not strength:
        m = re.search(r"(?<![A-Za-z0-9])C\d{2,3}(?!\d)", text)
        if m: strength = m.group(0).upper()
    if strength:
        specs["strength_grade"] = strength

    steel = features.get("钢筋等级", "")
    if not steel:
        m = re.search(r"HRB\d+E?|HPB\d+", text, re.I)
        if m: steel = m.group(0).upper()
    if steel:
        specs["steel_grade"] = steel

    dn = re.search(r"DN(\d+(?:\.\d+)?)", text)
    if dn: specs["dn_size"] = f"DN{dn.group(1)}"

    dia = re.search(r"[Φφ](\d+(?:\.\d+)?)", text)
    if dia: specs["diameter"] = f"Φ{dia.group(1)}"

    thick = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*厚", text)
    if thick: specs["thickness"] = f"{thick.group(1)}mm厚"

    mix = re.search(r"(\d+:\d+(?:\.\d+)?)", text)
    if mix: specs["mix_ratio"] = mix.group(1)

    fire_rating = re.search(r"B[12]级", text)
    if fire_rating: specs["fire_rating"] = fire_rating.group(0)

    perf = re.search(r"(\d+(?:\.\d+)?级)", text)
    if perf: specs["performance_grade"] = perf.group(1)

    dims = re.search(r"(\d+(?:\.\d+)?\s*[×xX]\s*\d+(?:\.\d+)?(?:\s*[×xX]\s*\d+(?:\.\d+)?)?\s*mm)", text)
    if dims: specs["dimensions"] = dims.group(1)

    return specs


# ══════════════════════════════════════
# 规格补齐核心 — 对单个材料执行完整的规格填充
# ══════════════════════════════════════

def _enrich_item_specs(item: dict, explicit_specs: dict[str, str], t3_spec_idx: dict) -> dict:
    """对单个采购材料执行规格补齐。返回更新后的 item。

    补齐优先级:
      1. 用户输入显式声明的规格 → 最高优先级，直接使用
      2. T3 规格模式定义的 default → 存在则使用，标注来源="T3默认"
      3. 必填但无值 → 标记为"待确认"，列出允许值供用户选择
      4. 以上都没有 → 保持 AI 的 spec_hint

    绝不编造取值——只能来自输入、T3标准、或标记待确认。
    """
    mat_name = item.get("material_name", "")
    mat_id = item.get("material_id", "").strip()
    existing_spec_hint = item.get("spec_hint", "")

    # Step 2.5a: 从材料名中拆分嵌入的规格
    core_name, split_spec, split_specs = _split_spec_from_name(mat_name, existing_spec_hint)
    if core_name != mat_name:
        item["material_name"] = core_name
        item["_name_before_spec_split"] = mat_name
    if split_spec:
        item["spec_hint"] = split_spec
    if split_specs:
        item["_extracted_specs"] = split_specs

    # 如果没有 T3 物料ID，尝试在当前材料名基础上模糊匹配 T3
    t3_entry = None
    if mat_id and mat_id in t3_spec_idx:
        t3_entry = t3_spec_idx[mat_id]
    else:
        # 在 T3 中按标准名称模糊查找
        clean_name = (core_name or mat_name).strip()
        for mid, entry in t3_spec_idx.items():
            std_name = entry.get("standard_name", "")
            if clean_name in std_name or std_name in clean_name:
                t3_entry = entry
                item["material_id"] = mid
                item["t3_name"] = std_name
                break

    # 如果还是没有 T3 条目，只做基本的输入规格注入
    if not t3_entry:
        item = _inject_explicit_specs_to_item(item, explicit_specs)
        item["spec_source"] = "AI推导+输入提取"
        return item

    # Step 2.5c: 按 T3 规格模式逐参数补齐
    spec_patterns = t3_entry.get("spec_patterns", [])
    new_spec_values = []
    spec_details = []
    missing_required = []

    for sp in spec_patterns:
        if not isinstance(sp, dict):
            continue
        param_name = sp.get("param", "")
        is_required = sp.get("required", False) is True
        default_val = sp.get("default", "")
        allowed_vals = sp.get("values", []) if isinstance(sp.get("values"), list) else []
        source_std = sp.get("source_standard", "")
        param_field = sp.get("param")  # 字段名如 "牌号", "公称直径"

        # 检查用户输入是否已声明此参数的值
        matched_value = _match_explicit_spec(param_name, param_field, explicit_specs, split_specs)

        if matched_value:
            # 来源: 用户输入
            new_spec_values.append(f"{param_name}={matched_value}")
            spec_details.append({
                "param": param_name,
                "value": matched_value,
                "source": "输入",
                "required": is_required,
                "standard": source_std,
            })
        elif default_val:
            # 来源: T3 默认值
            new_spec_values.append(f"{param_name}={default_val}")
            spec_details.append({
                "param": param_name,
                "value": default_val,
                "source": "T3默认",
                "required": is_required,
                "standard": source_std,
            })
        elif is_required:
            # 必填但无值 → 标记待确认
            allowed_hint = ""
            if allowed_vals:
                shown = allowed_vals[:8]
                allowed_hint = f"（可选: {', '.join(str(v) for v in shown)}）"
            new_spec_values.append(f"{param_name}=?待确认{allowed_hint}")
            missing_required.append({
                "param": param_name,
                "allowed_values": allowed_vals[:10],
                "standard": source_std,
            })
            spec_details.append({
                "param": param_name,
                "value": None,
                "source": "待确认",
                "required": True,
                "standard": source_std,
                "allowed_values": allowed_vals[:10],
            })

    # 合并规格值
    if new_spec_values:
        existing_specs = [x.strip() for x in re.split(r"[;；,，]", item.get("spec_hint", "")) if x.strip() and "=" not in x]
        final_spec = "；".join(existing_specs + new_spec_values)
        item["spec_hint"] = final_spec

    item["spec_details"] = spec_details
    item["spec_source"] = "T3标准物料库"
    if missing_required:
        item["missing_required_specs"] = missing_required
        item["spec_source"] += "（有必填规格待确认）"

    # 将 T3 标准信息写入 item
    if not item.get("t3_name"):
        item["t3_name"] = t3_entry.get("standard_name", "")
    if not item.get("category_path"):
        item["category_path"] = t3_entry.get("category_path", "")
    if not item.get("unit"):
        item["unit"] = t3_entry.get("unit", "")

    return item


def _match_explicit_spec(param_name: str, param_field: str, explicit_specs: dict[str, str], split_specs: list[dict]) -> str | None:
    """判断显式规格中是否有匹配该参数的取值。"""
    # 通过参数名/字段名的语义映射来匹配
    param_lower = (param_name + param_field).lower()

    field_map = {
        "牌号": ["steel_grade", "steel_grade_q", "strength_grade"],
        "强度等级": ["strength_grade"],
        "公称直径": ["dn_size", "diameter"],
        "直径": ["diameter", "dn_size"],
        "壁厚": ["thickness"],
        "厚度": ["thickness"],
        "防火等级": ["fire_rating"],
        "性能等级": ["performance_grade"],
        "截面型号": ["dimensions"],
        "尺寸": ["dimensions"],
        "配合比": ["mix_ratio"],
    }

    candidate_fields = []
    for kw, fields in field_map.items():
        if kw in param_lower:
            candidate_fields.extend(fields)

    # 从显式规格中查找
    for field in candidate_fields:
        if field in explicit_specs:
            return explicit_specs[field]

    # 从名字拆出的规格中查找
    for spec in split_specs:
        if spec.get("field") in candidate_fields:
            return spec.get("value")

    # 不执行兜底匹配——规格值必须能明确对应到参数，防止 C30 被误匹配到坍落度等无关参数
    return None


def _inject_explicit_specs_to_item(item: dict, explicit_specs: dict[str, str]) -> dict:
    """当材料没有 T3 规格模式时，将输入中提取的显式规格注入 spec_hint。"""
    mat_name = item.get("material_name", "")
    spec_hint = item.get("spec_hint", "")
    added = []

    # 混凝土材料 → 注入强度等级
    if "混凝土" in mat_name and "strength_grade" in explicit_specs and explicit_specs["strength_grade"] not in spec_hint:
        added.append(explicit_specs["strength_grade"])

    # 钢筋材料 → 注入钢筋等级
    if ("钢筋" in mat_name or "钢" in mat_name) and "steel_grade" in explicit_specs and explicit_specs["steel_grade"] not in spec_hint:
        added.append(explicit_specs["steel_grade"])

    # 管材 → 注入管径
    if ("管" in mat_name and "dn_size" in explicit_specs and explicit_specs["dn_size"] not in spec_hint):
        added.append(explicit_specs["dn_size"])

    if added:
        if spec_hint:
            spec_hint = spec_hint + "；" + "；".join(added)
        else:
            spec_hint = "；".join(added)
        item["spec_hint"] = spec_hint

    return item


def _enrich_all_specs(items: list[dict], explicit_specs: dict[str, str]) -> list[dict]:
    """对全部材料执行规格补齐。"""
    t3_spec_idx = _load_t3_spec_index()
    for item in items:
        _enrich_item_specs(item, explicit_specs, t3_spec_idx)
    return items


# ══════════════════════════════════════
# 知识库数据加载
# ══════════════════════════════════════
def load_t1():
    rows = []
    for p in [KB_FJ / "CSV导出/01_t1_分部代码路由.csv",
              KB_AZ / "CSV导出/01_t1_分部代码路由.csv"]:
        if p.exists():
            rows.extend(read_csv(p))
    return rows

def load_t2():
    rows = []
    for p in [KB_FJ / "CSV导出/02_t2_清单材料映射.csv",
              KB_AZ / "CSV导出/03_t2_清单材料映射.csv"]:
        if p.exists():
            rows.extend(read_csv(p))
    return rows

def load_t3():
    rows = []
    for p in [KB_FJ / "CSV导出/03_t3_标准物料库.csv",
              KB_AZ / "CSV导出/02_t3_标准物料库.csv"]:
        if p.exists():
            rows.extend(read_csv(p))
    return rows

def load_q1_q2_for_boq(boq_code_prefix: str):
    """加载与该BOQ编码前缀相关的Q1定额数据"""
    q1_all = read_csv(KB_01 / "Q1_定额索引.csv")
    q1_filtered = [r for r in q1_all if r.get("boq_code","").startswith(boq_code_prefix)]
    if not q1_filtered:
        q1_filtered = q1_all[:500]
    return q1_filtered


# ══════════════════════════════════════
# 标准工序链知识库加载
# ══════════════════════════════════════

_process_chains_cache = None

def _load_process_chains(force_reload: bool = False) -> dict[str, list[dict]]:
    """加载标准工序链CSV，返回 {boq_code: [process_steps]}。"""
    global _process_chains_cache
    if _process_chains_cache is not None and not force_reload:
        return _process_chains_cache
    _process_chains_cache = {}
    chain_path = ROOT / "标准知识库" / "源数据" / "附录L" / "process_chains.csv"
    if not chain_path.exists():
        return _process_chains_cache
    rows = read_csv(chain_path)
    for r in rows:
        code = r.get("boq_code", "").strip()
        if not code:
            continue
        try:
            step_num = int(r.get("step", "0"))
        except (ValueError, TypeError):
            step_num = 0
        is_main = r.get("is_main", "").strip().lower() == "true"
        entry = {
            "step": step_num,
            "process_name": r.get("process_name", "").strip(),
            "description": r.get("description", "").strip(),
            "main_material_types": r.get("main_material_types", "").strip(),
            "aux_material_types": r.get("aux_material_types", "").strip(),
            "is_main": is_main,
            "notes": r.get("notes", "").strip(),
        }
        _process_chains_cache.setdefault(code, []).append(entry)
    # 按 step 排序每个编码的工序
    for code in _process_chains_cache:
        _process_chains_cache[code].sort(key=lambda x: x["step"])
    return _process_chains_cache


# ══════════════════════════════════════
# AI 客户端（延迟导入）
# ══════════════════════════════════════
def ai_chat(messages, temperature=0.3, max_tokens=8192, thinking=True):
    try:
        from AI客户端 import chat
        result = chat(messages, temperature=temperature, max_tokens=max_tokens, thinking=thinking)
        if result.get("error"):
            _log_error(f"AI_CHAT error: {result['error']}")
        if not result.get("content") and not result.get("thinking"):
            _log_error(f"AI_CHAT empty response")
        return result
    except RuntimeError as e:
        _log_error(f"AI_CHAT RuntimeError: {e}")
        return {"content": "", "thinking": "", "error": str(e)}
    except Exception as e:
        import traceback
        _log_error(f"AI_CHAT Exception: {e}\n{traceback.format_exc()}")
        return {"content": "", "thinking": "", "error": f"AI调用失败: {e}"}

def ai_chat_json(messages, temperature=0.1, max_tokens=8192, thinking=True):
    try:
        print(f"[AI_CALL] Calling AI API, model={os.getenv('DEEPSEEK_MODEL') or os.getenv('MOONSHOT_MODEL','')}, msgs={len(messages)}, max_tokens={max_tokens}")
        from AI客户端 import chat_json
        result = chat_json(messages, temperature=temperature, max_tokens=max_tokens, thinking=thinking)
        print(f"[AI_CALL] Response: content_len={len(result.get('content','') or '')}, json_ok={result.get('json') is not None}, error={result.get('error','')[:100]}")
        return result
    except Exception as e:
        import traceback
        print(f"[AI_CALL ERROR] {e}")
        traceback.print_exc()
        return {"json": None, "content": "", "error": str(e)}

def _log_error(msg: str):
    """写错误日志到文件，方便调试"""
    try:
        from 配置 import DATA_DIR
        log_path = DATA_DIR / "ai_errors.log"
        from datetime import datetime
        timestamp = datetime.now().isoformat()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {msg}\n")
    except:
        pass
    print(f"[AI_ERROR] {msg}", file=sys.stderr)


# ══════════════════════════════════════
# 文本解析
# ══════════════════════════════════════
def parse_input(content: str) -> dict:
    fields = {"项目特征": {}, "备注": {}}
    aliases = {
        "项目编码": "项目编码", "清单编码": "项目编码",
        "项目名称": "项目名称", "名称": "项目名称",
        "计量单位": "计量单位", "单位": "计量单位",
        "工程量": "工程量", "数量": "工程量",
    }
    text = re.sub(r"\s+", " ", content.strip())
    compact = re.sub(r"\s+", "", content)

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line: continue
        m = re.match(r"^([^:：=]+)\s*[:：=]\s*(.+)$", line)
        if not m: continue
        key, value = m.group(1).strip(), m.group(2).strip()
        canonical = aliases.get(key)
        if canonical: fields[canonical] = value
        elif "备注" in key: fields["备注"][key] = value
        else: fields["项目特征"][key] = value

    if "项目编码" not in fields:
        m = re.search(r"(?<!\d)(\d{9,12})(?!\d)", compact)
        if m: fields["项目编码"] = m.group(1)
    if "工程量" not in fields:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(m³|m3|m²|m2|㎡|m|米|t|吨|kg|个|套|台)", text)
        if m:
            fields["工程量"] = m.group(1)
            fields.setdefault("计量单位", m.group(2))
    if "项目名称" not in fields:
        patterns = ["矩形柱","矩形梁","有梁板","无梁板","剪力墙","独立基础","条形基础",
                    "屋面卷材防水","低压碳钢管","消火栓钢管","电力电缆","桥架",
                    "框架柱","构造柱","过梁","圈梁","砖基础","设备基础","基础垫层"]
        hit = next((n for n in patterns if n in text), None)
        if hit: fields["项目名称"] = hit
    features = fields["项目特征"]
    features.setdefault("原文描述", content.strip())
    c = re.search(r"(?<![A-Za-z0-9])C\d{2,3}(?!\d)", text)
    if c: features.setdefault("混凝土强度等级", c.group(0).upper())
    s = re.search(r"(?<![A-Za-z0-9])HRB\d+E?|HPB\d+", text, re.I)
    if s: features.setdefault("钢筋等级", s.group(0).upper())
    dn = re.search(r"DN(\d+(?:\.\d+)?)", text)
    if dn: features.setdefault("公称直径", f"DN{dn.group(1)}")
    dia = re.search(r"[Φφ](\d+(?:\.\d+)?)", text)
    if dia: features.setdefault("直径", f"Φ{dia.group(1)}")
    thick = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*厚", text)
    if thick: features.setdefault("厚度", f"{thick.group(1)}mm厚")
    # 提取所有显式规格参数，供后续补齐使用
    explicit_specs = _extract_explicit_specs(features, features.get("原文描述", content))
    fields["_显式规格"] = explicit_specs
    if "项目编码" not in fields and "项目名称" not in fields:
        raise ValueError("无法识别输入，请提供清单编码或项目名称。")
    return {"工程量清单": fields}


# ══════════════════════════════════════
# T1/T2扩展检索辅助函数
# ══════════════════════════════════════

# 材料类型关键词模式 — 从项目特征中提取有工程意义的材料关键词
_MATERIAL_KEYWORD_PATTERNS = [
    # 门相关
    r"钢制保温门", r"钢质保温门", r"保温门", r"防盗门", r"防火门", r"卷帘门",
    r"铝合金门", r"塑钢门", r"彩板门", r"钢木大门", r"推拉门", r"感应门",
    r"全玻门", r"旋转门", r"特种门", r"屏蔽门", r"隔音门", r"防射线门",
    r"金属门", r"木质门", r"实木门", r"套装门",
    # 窗相关
    r"铝合金窗", r"塑钢窗", r"断桥铝窗", r"防火窗", r"百叶窗",
    # 安装辅材
    r"门锁", r"五金配件", r"门框", r"门套", r"窗套",
    # 通用材料类型
    r"C\d{2,3}", r"HRB\d+E?", r"DN\d+",
]
_MATERIAL_KW_COMPILED = [re.compile(p, re.I) for p in _MATERIAL_KEYWORD_PATTERNS]


def _extract_material_keywords(text: str) -> list[str]:
    """从项目特征文本中提取工程材料类型关键词。"""
    seen = set()
    result = []
    for pat in _MATERIAL_KW_COMPILED:
        for m in pat.finditer(text):
            kw = m.group(0)
            if kw not in seen:
                seen.add(kw)
                result.append(kw)
    return result


def _detect_code_feature_mismatch(code_prefix: str, feature_text: str,
                                   t1_siblings: list[dict], t1_hit: dict | None) -> str:
    """检测编码分类与项目特征描述之间的冲突。

    例如：编码010802=金属门，但特征描述"钢制保温门"→ 保温门属于010804特种门。
    """
    if not t1_hit or len(t1_siblings) < 2:
        return ""
    current_name = t1_hit.get("分部名称", "")
    feature_lower = feature_text.lower()
    # 检查特征关键词是否命中同品类其他子类
    mismatches = []
    for sib in t1_siblings:
        sib_name = sib.get("分部名称", "")
        if sib_name == current_name:
            continue
        typical = sib.get("典型清单项", "")
        kw_field = sib.get("关键词", "")
        # 检查典型清单项和关键词是否在特征文本中出现
        for item in _parse_json_field(typical):
            if item and len(item) >= 2 and item in feature_text:
                mismatches.append(f'特征中的“{item}”可能属于{sib_name}({sib.get("分部编码","")})而非{current_name}')
                break
        if not any(m for m in mismatches if sib_name in m):
            for kw in _parse_json_field(kw_field):
                if kw and len(kw) >= 2 and kw.lower() in feature_lower:
                    mismatches.append(f'特征关键词“{kw}”可能指向{sib_name}({sib.get("分部编码","")})而非{current_name}')
                    break
    return "；".join(mismatches[:3]) if mismatches else ""


def _build_category_taxonomy_context(code_prefix: str, t1_siblings: list[dict],
                                      t1_hit: dict | None) -> str:
    """构建国标分类上下文 — 告诉AI当前编码所属品类的完整子类结构。

    例如对于010802，输出：
      010801 木门、010802 金属门 ← 当前、010803 金属卷帘门、
      010804 特种门（含保温门/隔音门）、010805 其他门...
    """
    if not t1_hit or len(t1_siblings) <= 1:
        return ""
    lines = ["【国标分类参考】当前清单编码所属品类的完整子类结构："]
    seen_names = set()
    for sib in sorted(t1_siblings, key=lambda r: r.get("分部编码", "")):
        code = sib.get("分部编码", "")
        name = sib.get("分部名称", "")
        if name in seen_names:
            continue
        seen_names.add(name)
        is_current = (t1_hit and code == t1_hit.get("分部编码", ""))
        marker = " ← 当前编码匹配" if is_current else ""
        typical = sib.get("典型清单项", "")
        typical_str = f"（含: {typical}）" if typical else ""
        lines.append(f"  {code} {name}{marker} {typical_str}")
    lines.append("重要: 如果项目特征描述的材料类型与“当前编码匹配”子类不符，请以项目特征为准，从上述子类中选择最匹配的类型推导工序和材料。")
    return "\n".join(lines)


def _t2_row_to_summary(r: dict, priority_label: str) -> dict:
    """将T2规则行转为prompt用的摘要字典。"""
    return {
        "来源": priority_label,
        "映射编码": r.get("映射编码", ""),
        "材料名称": r.get("材料名称", ""),
        "材料角色": r.get("材料角色", ""),
        "物料ID": r.get("物料ID", ""),
        "消耗量公式": r.get("消耗量公式", ""),
        "损耗率": r.get("损耗率", ""),
        "单位": r.get("采购单位", ""),
        "供应方式": r.get("供应方式", ""),
        "匹配关键词": r.get("匹配关键词", ""),
        "必需特征": r.get("必需特征", ""),
    }


# ══════════════════════════════════════
# 主拆解流程
# ══════════════════════════════════════
def run_breakdown(content: str, province: str = "SN") -> dict:
    # ═══ Stage 0: 清单质量预检 ═══
    try:
        from boq_precheck import scan_boq_content
        pre_check = scan_boq_content(content)
    except ImportError:
        pre_check = None

    data = parse_input(content)
    print(f"===== RUN_BREAKDOWN (province={province}) =====", flush=True)
    boq = data["工程量清单"]
    boq_code = boq.get("项目编码", "")
    boq_name = boq.get("项目名称", "")
    features = boq.get("项目特征", {})
    feature_text = features.get("原文描述", "")
    explicit_specs = boq.get("_显式规格", {})
    qty_text = boq.get("工程量", "1")
    try: boq_qty = float(re.search(r"[\d.]+", str(qty_text)).group())
    except: boq_qty = 1.0

    # ═══ 加载省份上下文 ═══
    prov_ctx = load_province_context(province)
    province_description = prov_ctx.describe_for_prompt() if prov_ctx.is_configured else ""
    province_rules_applied: list[dict] = []
    province_exclusions: list[dict] = []

    code_prefix = boq_code[:6] if len(boq_code) >= 6 else ""
    t1_all, t2_all, t3_all = load_t1(), load_t2(), load_t3()
    t3_idx = {r.get("物料ID","").strip(): r for r in t3_all}

    # ═══ Step 0: KB快速查询（含父级品类扩展 + 特征关键词跨区搜索）═══
    t1_hit = None
    parent_prefix = code_prefix[:4] if len(code_prefix) >= 4 else ""
    t1_siblings: list[dict] = []
    if code_prefix:
        for r in t1_all:
            rc = r.get("分部编码","").strip()
            if rc == code_prefix:
                t1_hit = r
            if parent_prefix and rc.startswith(parent_prefix):
                t1_siblings.append(r)

    # T2: 精确匹配 + 父级品类扩展 + 特征关键词跨区搜索
    t2_exact: list[dict] = []
    t2_parent: list[dict] = []
    t2_keyword: list[dict] = []
    if code_prefix:
        feature_text_all = f"{feature_text} {boq_name} {' '.join(str(v) for v in features.values() if isinstance(v, str))}"
        feature_kw = _extract_material_keywords(feature_text_all)
        for r in t2_all:
            rc = r.get("分部编码","").strip()
            if rc == code_prefix:
                t2_exact.append(r)
            elif parent_prefix and rc.startswith(parent_prefix):
                t2_parent.append(r)
            elif feature_kw:
                kws = _parse_json_field(r.get("匹配关键词",""))
                mat_name = r.get("材料名称","")
                for kw in feature_kw:
                    if kw and (kw in mat_name or any(kw in k for k in kws)):
                        t2_keyword.append(r)
                        break
    t2_candidates = t2_exact + t2_parent + t2_keyword
    _code_feature_mismatch = _detect_code_feature_mismatch(code_prefix, feature_text_all, t1_siblings, t1_hit)
    _category_taxonomy_text = _build_category_taxonomy_context(code_prefix, t1_siblings, t1_hit)

    # ═══ 加载标准工序链KB ═══
    process_chains_kb = _load_process_chains()
    standard_code = boq_code[:9] if len(boq_code) >= 9 else boq_code[:6]
    pre_defined_chain = process_chains_kb.get(standard_code, [])
    process_chain_injection = ""
    if pre_defined_chain:
        process_chain_injection = f"""
【系统预置标准工序链 — 来自工序知识库，不可删除/重排/is_main不可修改】:
{json.dumps([{{
    'step': c['step'],
    'name': c['process_name'],
    'description': c['description'],
    'main_material_types': c['main_material_types'],
    'aux_material_types': c['aux_material_types'],
    'is_main': c['is_main'],
}} for c in pre_defined_chain], ensure_ascii=False, indent=2)}

请基于以上标准工序链输出工序JSON。严格约束：
1. 必须保留所有标准工序的 step/name/is_main（不可修改）
2. 可在 description 中补充更具体的施工细节
3. 可在 typical_materials 中补充具体材料名
4. 不可删除、重排序、或改变 is_main 标记
5. 如清单特征有明显特殊工序需要增加，新增工序标记 source="ai_supplement"
"""

    # ═══ Step 1: AI 工序还原（工序链KB做grounding）═══
    t1_info_text = ""
    if t1_hit:
        t1_info_text = f"""T1路由结果: 分部编码={t1_hit.get('分部编码')}, 分部名称={t1_hit.get('分部名称')}, 子目名称={t1_hit.get('子目名称')}
典型清单项: {t1_hit.get('典型清单项','')}
关键词: {t1_hit.get('关键词','')}"""
    if _code_feature_mismatch:
        t1_info_text += f"\n\n⚠ 编码-特征冲突提示: {_code_feature_mismatch}"
    if _category_taxonomy_text:
        t1_info_text += f"\n\n{_category_taxonomy_text}"

    step1_result = ai_chat_json([
        {"role":"system","content":"""你是建筑工程造价专家。给定一条工程量清单项，请还原其标准施工工序链。

输出JSON格式: {"processes":[{"step":1,"name":"工序名","description":"具体做什么","typical_materials":"该工序会涉及的典型材料类别","is_main":true/false}]}

规则:
1. 工序必须按施工先后顺序排列
2. is_main=true 表示该工序消耗主要材料（需纳入采购清单）
3. is_main=false 表示措施工序（模板支架等）或辅助工序
4. 参考GB50500标准和实际施工规范"""},
        {"role":"user","content":f"""请还原以下清单项的施工工序链:

清单编码: {boq_code}
清单名称: {boq_name}
项目特征: {json.dumps(features, ensure_ascii=False)}
工程量: {boq_qty} {boq.get('计量单位','')}

{t1_info_text}

项目所在地信息:
{province_description}
{process_chain_injection}
请输出该清单项的标准施工工序链（需考虑地区气候和供暖特性）。"""}
    ], temperature=0.1, max_tokens=8192, thinking=True)

    ai_processes = step1_result.get("json", {}).get("processes", []) if step1_result.get("json") else []
    if not ai_processes:
        # 回退: 根据T1信息生成简单工序
        t1_kw = _parse_json_field(t1_hit.get("关键词","")) if t1_hit else []
        ai_processes = [{"step":1,"name":boq_name or "清单项","description":"根据清单描述","typical_materials":",".join(t1_kw),"is_main":True}]

    # ═══ Step 2: 材料推导（裁决引擎优先 + AI补充）═══
    # 先尝试裁决引擎，命中规则则用规则；无规则时回退到AI
    decision_result = None
    ai_materials = []
    try:
        from material_decision_engine import (
            apply_material_decisions, match_forbidden_materials,
            decision_to_pipeline_format, DecisionResult,
        )
        project_ctx = {
            "building_part": features.get("部位", ""),
            "structure_type": features.get("结构类型", ""),
        }
        decision_result = apply_material_decisions(
            boq_code, boq_name, feature_text, features, project_ctx,
        )
        if decision_result and decision_result.included_materials:
            # 裁决引擎命中 → 作为主源，AI仅补充
            decision_mats = decision_to_pipeline_format(decision_result)
            ai_materials = decision_mats
            print(f"[裁决引擎] {boq_code}: 规则命中{decision_result.total_rules_matched}条, "
                  f"确定{len(decision_result.included_materials)}种材料, "
                  f"S1={len(decision_result.aux_s1_materials)} S2={len(decision_result.aux_s2_materials)} S3={len(decision_result.aux_s3_materials)}")
    except ImportError as e:
        print(f"[裁决引擎] 模块不可用: {e}")

    # 裁决引擎未命中时回退到AI推理
    if not ai_materials:
        # 构建T2候选材料的context注入prompt，按优先级分组
        t2_parts = []
        if t2_exact:
            exact_summary = [_t2_row_to_summary(r, "精确匹配") for r in t2_exact[:20]]
            t2_parts.append(f"【当前编码 {code_prefix} 的T2规则 — 最高优先级】:\n{json.dumps(exact_summary, ensure_ascii=False, indent=2)}")
        if t2_parent:
            parent_summary = [_t2_row_to_summary(r, "相邻子类") for r in t2_parent[:10]]
            t2_parts.append(f"【同父级品类 {parent_prefix}xx 其他子类的T2规则 — 参考优先级】:\n{json.dumps(parent_summary, ensure_ascii=False, indent=2)}")
        if t2_keyword:
            kw_summary = [_t2_row_to_summary(r, "关键词命中") for r in t2_keyword[:5]]
            t2_parts.append(f"【特征关键词跨区命中的T2规则 — 降级参考】:\n{json.dumps(kw_summary, ensure_ascii=False, indent=2)}")
        t2_context = "\n\n".join(t2_parts)
        if t2_context:
            t2_context = f"""\n\n【知识库T2规则参考 — 按优先级分组】:
    {t2_context}

    注意:
    1. "精确匹配"规则优先级最高，但如果项目特征描述的材料类型与当前编码分类不符，请优先以项目特征为准
    2. "相邻子类"和"关键词命中"规则提示当前清单项可能实际属于其他子类，请认真判断
    3. 禁止引入与清单项工程领域无关的材料（如：门窗工程不应引入冷藏设备相关材料）"""

    if not ai_materials:
        step2_result = ai_chat_json([
        {"role":"system","content":"""你是建筑工程采购专家。给定施工工序链和清单特征，请推导每道主工序需要采购的材料。

输出JSON格式:
{"materials":[{"material_name":"材料通用名称（不含规格）","spec_hint":"具体规格/型号/等级参数","role":"主材/辅材/措施材料","unit":"采购单位","supply":"甲供 or 乙供","waste_rate_estimate":0.025,"confidence":"high/medium/low","reason":"为什么需要这个材料","source":"T2规则/专业经验/定额推导"}]}

规则:
1. 优先使用知识库T2规则（source标为"T2规则"）
2. T2规则不完全匹配时，用专业经验补充（source标为"专业经验"）
3. 辅材/措施材料可标注但不一定进采购清单
4. 材料名称使用行业通用名称，**不要包含规格参数在名称中**
5. waste_rate_estimate 是小数形式（如2.5%写0.025）
6. confidence 基于: T2规则且关键词匹配→high, 经验推导→medium, 不确定→low
7. **【重要】spec_hint 必须认真填写**
8. **【关键约束】材料类型必须与清单项的工程领域一致**:
   - 门窗工程只应推导门/窗成品、五金配件、密封胶、发泡剂、锚固件等安装辅材
   - 禁止将通用关键词匹配到无关领域的材料（如"保温门"不应推导为冷藏库门，应推导为钢制保温门或特种保温门成品）
   - 当编码分类与项目特征描述冲突时，以项目特征的实质描述为准"""},
        {"role":"user","content":f"""请推导以下清单项需要的采购材料（注意提取规格参数到 spec_hint):

清单编码: {boq_code}
清单名称: {boq_name}
项目特征: {json.dumps(features, ensure_ascii=False)}
工程量: {boq_qty} {boq.get('计量单位','')}

项目所在地信息:
{province_description}

AI识别的施工工序:
{json.dumps(ai_processes, ensure_ascii=False, indent=2)}
{t2_context}

请输出该清单项应该采购的材料清单（去重，每种材料只出现一次）。材料名称不含规格，规格写在 spec_hint 中。注意: 不要包含项目所在地不适用的材料类型。"""}
    ], temperature=0.2, max_tokens=8192, thinking=True)

        ai_materials = step2_result.get("json", {}).get("materials", []) if step2_result.get("json") else []
    
    # AI返回后，用裁决引擎的FORBIDDEN/EXCLUDED规则过滤
    if ai_materials and decision_result:
        to_exclude = match_forbidden_materials(ai_materials, boq_code, feature_text)
        if to_exclude:
            exclude_names = {e["material_name"] for e in to_exclude}
            ai_materials = [m for m in ai_materials if m.get("material_name") not in exclude_names]
            print(f"[裁决引擎] 过滤禁止材料: {[e['material_name'] for e in to_exclude]}")

    # 如果AI和裁决引擎都没有返回材料，回退到纯KB匹配
    if not ai_materials:
        ai_materials = _kb_fallback_materials(boq_name, t2_candidates)

    # ═══ Step 2.5: 规格补齐（KB驱动，不走AI）═══
    # 对 AI 返回的材料做规格拆分+输入注入+T3规格模式匹配
    _pre_items = []
    for am in ai_materials:
        _pre_items.append({
            "material_name": am.get("material_name", ""),
            "material_id": am.get("material_id", ""),
            "spec_hint": am.get("spec_hint", ""),
            "role": am.get("role", "主材"),
            "unit": am.get("unit", ""),
            "supply": am.get("supply", "乙供"),
        })
    _pre_items = _enrich_all_specs(_pre_items, explicit_specs)
    # 将补齐后的规格回写到 ai_materials
    for i, am in enumerate(ai_materials):
        if i < len(_pre_items):
            am["spec_hint"] = _pre_items[i].get("spec_hint", am.get("spec_hint", ""))
            am["_spec_details"] = _pre_items[i].get("spec_details", [])
            am["_missing_required_specs"] = _pre_items[i].get("missing_required_specs", [])
            if _pre_items[i].get("material_name") != am.get("material_name"):
                am["_name_before_spec_split"] = am.get("material_name", "")
                am["material_name"] = _pre_items[i].get("material_name", am.get("material_name", ""))

    # ═══ Step 3: 确定性计算（KB计算，不走AI）═══
    items = []
    for am in ai_materials:
        mat_name = am.get("material_name", "")
        mat_id = ""
        t3_info = {}
        formula = ""
        coeff = 1.0
        loss_rate = float(am.get("waste_rate_estimate", 0) or 0)
        loss_rate_source = "AI估算"  # 损耗率来源标注

        # 在T2候选中匹配
        best_t2 = _match_t2(mat_name, t2_candidates, boq_name)
        if best_t2:
            mat_id = best_t2.get("物料ID","").strip()
            t3_info = t3_idx.get(mat_id, {})
            formula = best_t2.get("消耗量公式","").strip()
            t2_map_code = best_t2.get("映射编码","").strip()  # ★用于省份覆盖
            # T2的损耗率优先
            t2_loss = best_t2.get("损耗率","").strip()
            if t2_loss:
                try: loss_rate = float(t2_loss)
                except: pass
                loss_rate_source = f"T2规则({t2_map_code})" if t2_map_code else "T2规则"
            # T2的系数
            coeff_field = best_t2.get("系数","").strip()
            if coeff_field:
                try: coeff = float(coeff_field)
                except: pass
            else:
                # 从公式中解析
                fm = re.match(r"(\d+\.?\d*)", formula) if formula else None
                if fm: coeff = float(fm.group(1))
            unit = best_t2.get("采购单位","").strip() or am.get("unit","")
            supply = best_t2.get("供应方式","").strip() or am.get("supply","乙供")
            role = best_t2.get("材料角色","").strip() or am.get("role","主材")
            map_code = t2_map_code
            verification = best_t2.get("验证状态","").strip()
            source_ref = best_t2.get("来源引用","").strip()

            # ★省份参数覆盖: 损耗率 (编码/物料ID/名称三级匹配)
            if prov_ctx.is_configured and t2_map_code:
                mname_lower = mat_name.lower()
                mname_chunks = [w for w in mname_lower.replace('(',' ').replace(')',' ').replace('/',' ').split() if len(w) >= 3]
                for (mc, pn), prov_override in prov_ctx.param_overrides.items():
                    if prov_override.diff_tag == "SAME":
                        continue
                    desc_full = (prov_override.override_desc + prov_override.quota_source).lower()
                    if (mc == t2_map_code or
                        mat_name.lower() in desc_full or
                        any(chunk in desc_full for chunk in mname_chunks)):
                        try:
                            ov_val = float(prov_override.override_value)
                            if ov_val <= 1.0:  # 小数=损耗率
                                loss_rate = ov_val
                                loss_rate_source = prov_override.quota_source
                                province_rules_applied.append({
                                    "type": "param_override",
                                    "material": mat_name,
                                    "map_code": mc,
                                    "param": pn,
                                    "base": prov_override.base_value,
                                    "override": prov_override.override_value,
                                    "source": prov_override.quota_source,
                                })
                            # else: >1.0 系数在下轮处理
                            break
                        except (ValueError, TypeError):
                            pass
        else:
            # AI推导的材料，无T2规则
            unit = am.get("unit","")
            supply = am.get("supply","乙供")
            role = am.get("role","主材")
            map_code = ""
            verification = ""
            source_ref = ""
            # 尝试在T3中模糊匹配
            for t3r in t3_all:
                if mat_name in t3r.get("标准名称",""):
                    mat_id = t3r.get("物料ID","").strip()
                    t3_info = t3r
                    break

        # ★省份损耗率/系数覆盖: param_overrides (第二轮)
        # 三级匹配: (1)T2映射编码精确 (2)物料ID匹配 (3)材料名关键词+参数名匹配
        if prov_ctx.is_configured:
            applied_override = False
            mat_name_lower = mat_name.lower()
            # 从材料名提取关键词(2字以上的独立词语)
            name_chars = set(mat_name_lower)
            for (mc, pn), prov_ov in prov_ctx.param_overrides.items():
                if prov_ov.diff_tag == "SAME":
                    continue
                # 匹配策略: 编码精确 OR 物料ID出现 OR 材料名在覆盖描述/定额出处中
                code_match = bool(mc == map_code) if map_code else False
                id_match = bool(mat_id and (mat_id in mc or mc in mat_id))
                desc_lower = (prov_ov.override_desc + prov_ov.quota_source).lower()
                # 材料名关键词匹配: 材料名中有意义的词在描述中出现
                name_chunks = [w for w in mat_name_lower.replace('(',' ').replace(')',' ').replace('/',' ').split() if len(w) >= 2]
                name_match = any(chunk in desc_lower for chunk in name_chunks if len(chunk) >= 3)
                if not (code_match or id_match or name_match):
                    continue
                try:
                    ov_val = float(prov_ov.override_value)
                    if ov_val > 1.0:
                        coeff = ov_val
                        province_rules_applied.append({
                            "type": "coeff_override",
                            "material": mat_name,
                            "map_code": mc,
                            "param": pn,
                            "base": prov_ov.base_value,
                            "override": prov_ov.override_value,
                            "source": prov_ov.quota_source,
                        })
                        applied_override = True
                    else:
                        loss_rate = ov_val
                        loss_rate_source = prov_ov.quota_source
                        province_rules_applied.append({
                            "type": "param_override",
                            "material": mat_name,
                            "map_code": mc,
                            "param": pn,
                            "base": prov_ov.base_value,
                            "override": prov_ov.override_value,
                            "source": prov_ov.quota_source,
                        })
                        applied_override = True
                    break  # 一个材料只应用一个覆盖
                except (ValueError, TypeError):
                    pass

        # ★省份材料规则: CONSUMPTION_ADJUST + COEFF_OVERRIDE
        # 2×2匹配: 物料ID精确(score>0) + 材料名关键词独立补充
        prov_rules_for_item = match_material_rules(
            prov_ctx.material_rules, mat_id, code_prefix
        ) if prov_ctx.is_configured else []

        # 名称关键词匹配(独立于ID匹配, 补漏物料ID不一致的情况)
        if prov_ctx.is_configured:
            mat_name_lower = mat_name.lower()
            existing_rows = {pr.row_index for pr in prov_rules_for_item}
            for pr in prov_ctx.material_rules:
                if pr.row_index in existing_rows:
                    continue
                if pr.section_code and pr.section_code != code_prefix:
                    continue
                if mat_name_lower in pr.reason.lower():
                    prov_rules_for_item.append(pr)
                elif pr.material_id and pr.material_id.lower() in mat_name_lower:
                    prov_rules_for_item.append(pr)
                elif t3_info and t3_info.get("标准名称","").lower() in pr.reason.lower():
                    prov_rules_for_item.append(pr)

        for pr in prov_rules_for_item:
            if pr.rule_type == "CONSUMPTION_ADJUST":
                try:
                    adj = float(pr.adjust_coeff) if pr.adjust_coeff else 1.0
                    coeff = coeff * adj
                    province_rules_applied.append({
                        "type": "consumption_adjust",
                        "material": mat_name,
                        "material_id": mat_id,
                        "adjust_coeff": adj,
                        "reason": pr.reason,
                    })
                except (ValueError, TypeError):
                    pass
            elif pr.rule_type == "COEFF_OVERRIDE" and pr.adjust_coeff:
                try:
                    coeff = float(pr.adjust_coeff)
                    province_rules_applied.append({
                        "type": "coeff_override",
                        "material": mat_name,
                        "material_id": mat_id,
                        "coeff": coeff,
                        "reason": pr.reason,
                    })
                except (ValueError, TypeError):
                    pass

        # ★省份T5维度系数
        if prov_ctx.is_configured and prov_ctx.t5_dimensions:
            t5_match = match_t5_dimension(
                prov_ctx.t5_dimensions, code_prefix,
                {"pipe_type": mat_name, "material_name": mat_name}
            )
            if t5_match and t5_match.coefficient != 1.0:
                coeff = t5_match.coefficient
                province_rules_applied.append({
                    "type": "t5_dimension",
                    "material": mat_name,
                    "section": code_prefix,
                    "dim_name": t5_match.dim_name,
                    "coeff": coeff,
                    "source": t5_match.source,
                })

        suggested_qty = round(boq_qty * coeff, 3)

        confidence = am.get("confidence","medium")
        if best_t2 and confidence == "high": pass  # AI和T2都确认→保持high
        elif best_t2: confidence = "high"
        elif confidence == "high": confidence = "medium"

        item = {
            "id": uuid.uuid4().hex[:8],
            "material_name": mat_name,
            "material_id": mat_id,
            "t3_name": t3_info.get("标准名称","").strip(),
            "category_path": t3_info.get("品类展示路径","").strip(),
            "role": role,
            "aux_grade": am.get("aux_grade"),
            "source_type": am.get("source", "AI推理"),
            "formula": formula,
            "coefficient": round(coeff, 4),
            "suggested_quantity": suggested_qty,
            "unit": unit,
            "supply": supply,
            "waste_rate": loss_rate,
            "confidence": confidence,
            "confidence_reason": f"AI推理 | 来源:{am.get('source','专业经验')} | {am.get('reason','')[:80]}",
            "map_code": map_code,
            "verification": verification,
            "source_ref": source_ref,
            "spec_hint": am.get("spec_hint",""),
            "spec_details": am.get("_spec_details", []),
            "missing_required_specs": am.get("_missing_required_specs", []),
            "spec_source": am.get("spec_source", ""),
            "name_before_spec_split": am.get("_name_before_spec_split", ""),
            "loss_rate_source": loss_rate_source,
            }

        # ═══ Step 3b: 透明消耗量分解 ═══
        try:
            from consumption_calc import calculate_consumption, breakdown_to_item_fields
            # 提取显式厚度
            explicit_thickness = None
            thick_match = re.search(r'(\d+(?:\.\d+)?)\s*mm', str(features.get("面层厚度", "")) or str(features.get("厚度", "")))
            if not thick_match:
                thick_match = re.search(r'(\d+(?:\.\d+)?)\s*mm', feature_text)
            if thick_match:
                try:
                    explicit_thickness = float(thick_match.group(1)) / 1000.0
                except (ValueError, TypeError):
                    pass
            breakdown = calculate_consumption(
                boq_quantity=boq_qty,
                boq_unit=boq.get('计量单位', ''),
                material_name=item.get('material_name', ''),
                material_id=item.get('material_id', ''),
                explicit_thickness=explicit_thickness,
                t2_rule=best_t2 if best_t2 else None,
                cat_loss_rate=item.get('standard_loss_rate'),
            )
            item.update(breakdown_to_item_fields(breakdown))
        except ImportError:
            pass

        items.append(item)

    # ═══ Step 4: 省份EXCLUDE规则 + KB校验 ═══
    # 省份材料排除 —— 两阶段匹配:
    #   阶段1: 按物料ID精确排除
    #   阶段2: 按材料名称关键词排除（兜底）
    if prov_ctx.is_configured:
        excluded_items = []
        kept_items = []
        for item in items:
            item_rules = match_material_rules(
                prov_ctx.material_rules,
                item.get("material_id", ""),
                code_prefix,
            )
            should_exclude = False
            exclude_reason = ""

            # 阶段1: 物料ID精确匹配
            for pr in item_rules:
                if pr.rule_type == "EXCLUDE":
                    should_exclude = True
                    exclude_reason = pr.reason
                    break

            # 阶段2: 未匹配的EXCLUDE规则，按材料名称关键词匹配
            if not should_exclude:
                mat_name_lower = item["material_name"].lower()
                sec_rule_excl = False
                for pr in prov_ctx.material_rules:
                    if pr.rule_type != "EXCLUDE":
                        continue
                    # 该规则的物料ID已精确匹配过，跳过
                    if pr.material_id and pr.material_id == item.get("material_id", ""):
                        continue
                    # 检查分部编码匹配 (空=全分部, 精确匹配)
                    if pr.section_code and pr.section_code != code_prefix:
                        continue
                    # 按规则中的关键词匹配材料名
                    reason_lower = pr.reason.lower()
                    # 从reason中提取材料关键词: "散热器在广东不适用" → 散热器
                    keywords = [item["material_name"].lower()]
                    # 也从T3物料标准名中补充匹配
                    if item.get("t3_name"):
                        keywords.append(item["t3_name"].lower())
                    if item.get("material_id"):
                        keywords.append(item["material_id"].lower())

                    matched = False
                    for kw in keywords:
                        if kw and len(kw) > 1 and kw in reason_lower:
                            matched = True
                            break
                    # 反向: reason中的关键术语是否在材料名中
                    if not matched:
                        # 简单的关键词提取: "A在广东不适用" → 找"A"
                        import re as _re
                        reason_terms = _re.split(r'[在广东北京陕西不适用无需沿海用]', pr.reason)
                        for term in reason_terms:
                            term = term.strip()
                            if term and len(term) > 1 and term.lower() in mat_name_lower:
                                matched = True
                                break
                    if matched:
                        should_exclude = True
                        exclude_reason = pr.reason + "（关键词匹配）"
                        break

            if should_exclude:
                province_exclusions.append({
                    "material_name": item["material_name"],
                    "material_id": item.get("material_id", ""),
                    "section_code": code_prefix,
                    "reason": exclude_reason,
                })
                excluded_items.append(item)
            else:
                kept_items.append(item)
        if excluded_items:
            print(f"[省份排除] {province}: 移除 {len(excluded_items)} 种材料: {[e['material_name'] for e in excluded_items]}")
            items = kept_items

    # 品类树损耗率
    try:
        cat_map = read_csv(KB_CAT / "T3_品类映射.csv")
        cat_idx = {r["leaf_id"]: r for r in read_csv(KB_CAT / "品类节点索引.csv")}
        for item in items:
            if not item["material_id"]: continue
            for cm in cat_map:
                if cm.get("material_id","").strip() == item["material_id"]:
                    lid = cm.get("leaf_id","").strip()
                    lr = cm.get("standard_loss_rate","").strip()
                    if not lr and lid in cat_idx:
                        lr = cat_idx[lid].get("loss_rate","")
                    if lr:
                        try: item["standard_loss_rate"] = float(lr)
                        except: pass
                    break
    except: pass

    # 规范数据
    n5_all = read_csv(KB_NORM / "N5_材料规范映射.csv")
    n5_set = set(n.get("material_id","").strip() for n in n5_all)
    for item in items:
        item["has_standard"] = item["material_id"] in n5_set

    # ═══ Step 5: AI 可信度评估（含规格完整性检查）═══
    items_for_review = [
        {"material_name": i["material_name"], "t3_name": i.get("t3_name",""),
         "confidence": i["confidence"], "role": i["role"], "unit": i["unit"],
         "spec_hint": i.get("spec_hint",""), "spec_source": i.get("spec_source",""),
         "missing_required_specs": i.get("missing_required_specs", []),
         "has_kb_rule": bool(i["map_code"]), "has_standard": i["has_standard"],
         "loss_rate_source": i.get("loss_rate_source", ""),
         "province_adjustments": [
             adj for adj in province_rules_applied
             if adj.get("material") == i["material_name"]
         ][:3]}
        for i in items
    ]
    province_excl_desc = ""
    if province_exclusions:
        province_excl_desc = f"\n省份排除材料: {json.dumps(province_exclusions, ensure_ascii=False)}"

    step5_result = ai_chat_json([
        {"role":"system","content":"""你是建筑工程质量审核专家。请审核采购材料清单，评估是否有漏项、规格缺失或错误。

输出JSON格式: {"review":{"missing_materials":[{"name":"可能遗漏的材料","reason":"原因","severity":"high/medium/low"}],"wrong_materials":[{"name":"可能有误的材料","reason":"原因"}],"spec_issues":[{"material":"材料名","issue":"规格问题描述","suggestion":"建议补充的规格"}],"overall_quality":"excellent/good/fair/poor","notes":"总体审核意见"}}

评估要点:
1. 清单项的主材是否齐全
2. 常用辅材/措施材料是否遗漏（如模板、养护剂、垫块、扎丝等）
3. 材料规格是否完整合理——注意检查 spec_hint 是否为空，missing_required_specs 中标记需人工确认的项
4. 甲供/乙供划分是否正确
5. 如果 spec_source 标记为"待确认"，请给出具体建议
6. 如项目在特殊地区(沿海/无供暖)，检查是否有不适用的材料"""},
        {"role":"user","content":f"""请审核以下采购材料清单（重点检查规格完整性）:

原始清单: {boq_code} {boq_name}, 工程量 {boq_qty} {boq.get('计量单位','')}
项目特征: {json.dumps(features, ensure_ascii=False)}
施工工序: {json.dumps([p.get('name','') for p in ai_processes], ensure_ascii=False)}
用户输入的显式规格: {json.dumps(explicit_specs, ensure_ascii=False)}
项目所在地: {province_description}
{province_excl_desc}

材料清单（含规格信息）:
{json.dumps(items_for_review, ensure_ascii=False, indent=2)}

请评估是否有遗漏或规格问题，输出审核意见。特别关注 spec_hint 为空和 missing_required_specs 不为空的材料。"""}
    ], temperature=0.1, max_tokens=8192, thinking=True)

    review = step5_result.get("json", {}).get("review", {}) if step5_result.get("json") else {}

    # 规格统计
    items_with_spec = sum(1 for i in items if i.get("spec_hint"))
    items_missing_required_spec = sum(1 for i in items if i.get("missing_required_specs"))
    items_with_t3_spec = sum(1 for i in items if i.get("spec_source","").startswith("T3"))

    return {
        "ok": True,
        "run_id": uuid.uuid4().hex[:10],
        "input": boq,
        "pre_check": {
            "overall_quality": pre_check.overall_quality,
            "quality_score": pre_check.quality_score,
            "has_external_ref": pre_check.has_external_ref,
            "external_refs": pre_check.external_refs,
            "missing_fields": pre_check.missing_fields,
            "code_name_match": pre_check.code_name_match,
            "code_name_detail": pre_check.code_name_detail,
            "issues": pre_check.issues,
            "summary": pre_check.summary,
        } if pre_check else None,
        "explicit_specs": explicit_specs,
        "t1_route": t1_hit.get("分部名称","") if t1_hit else "未匹配",
        "t1_code_prefix": code_prefix,
        "province": province,
        "province_profile": {
            "code": prov_ctx.profile.code,
            "name": prov_ctx.profile.name,
            "climate_zone": prov_ctx.profile.climate_zone,
            "is_coastal": prov_ctx.profile.is_coastal,
            "has_heating": prov_ctx.profile.has_heating,
            "is_configured": prov_ctx.is_configured,
        } if prov_ctx.profile else None,
        "province_rules_applied": province_rules_applied,
        "province_exclusions": province_exclusions,
        "ai_processes": ai_processes,
        "items": items,
        "item_count": len(items),
        "high_confidence": sum(1 for i in items if i["confidence"]=="high"),
        "medium_confidence": sum(1 for i in items if i["confidence"]=="medium"),
        "low_confidence": sum(1 for i in items if i["confidence"]=="low"),
        "items_with_spec": items_with_spec,
        "items_missing_required_spec": items_missing_required_spec,
        "items_with_t3_spec": items_with_t3_spec,
        "decision_engine": {
            "enabled": decision_result is not None,
            "rules_matched": decision_result.total_rules_matched if decision_result else 0,
            "materials_from_rules": len(decision_result.included_materials) if decision_result else 0,
            "s1_aux_count": len(decision_result.aux_s1_materials) if decision_result else 0,
            "s2_aux_count": len(decision_result.aux_s2_materials) if decision_result else 0,
            "s3_aux_count": len(decision_result.aux_s3_materials) if decision_result else 0,
            "forbidden_blocked": len(decision_result.forbidden_materials) if decision_result else 0,
            "excluded_materials": [m.material_name for m in decision_result.excluded_materials] if decision_result else [],
        },
        "ai_review": review,
        "ai_thinking": {
            "step1": step1_result.get("thinking","")[:500],
            "step2": step2_result.get("thinking","")[:500],
            "step5": step5_result.get("thinking","")[:500],
        },
    }


# ══════════════════════════════════════
# KB回退匹配（AI不可用时）
# ══════════════════════════════════════
def _match_t2(mat_name: str, t2_candidates: list, boq_name: str) -> dict | None:
    """在T2候选中按关键词找最佳匹配"""
    best, best_score = None, 0
    for r in t2_candidates:
        r_mat = r.get("材料名称","")
        kws = _parse_json_field(r.get("匹配关键词",""))
        score = 0
        if mat_name in r_mat or r_mat in mat_name: score += 10
        for kw in kws:
            if kw in boq_name: score += len(kw)
            if kw in mat_name: score += len(kw)
        if score > best_score:
            best_score, best = score, r
    return best if best_score >= 1 else None


def _kb_fallback_materials(boq_name: str, t2_candidates: list) -> list:
    """纯KB回退: 从T2候选中用关键词筛选"""
    scored = []
    for r in t2_candidates:
        kws = _parse_json_field(r.get("匹配关键词",""))
        score = sum(len(kw) for kw in kws if kw in boq_name)
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    seen = set()
    result = []
    for score, r in scored:
        mat_id = r.get("物料ID","").strip()
        if mat_id in seen: continue
        seen.add(mat_id)
        result.append({
            "material_name": r.get("材料名称",""),
            "spec_hint": "",
            "role": r.get("材料角色","主材"),
            "unit": r.get("采购单位",""),
            "supply": r.get("供应方式","乙供"),
            "waste_rate_estimate": float(r.get("损耗率","0") or 0),
            "confidence": "medium",
            "reason": "T2规则匹配（AI不可用）",
            "source": "T2规则",
        })
    return result


def run_validation(material_id: str, params: dict) -> dict:
    """材料参数校验（调用国家规范库）"""
    try:
        sys.path.insert(0, str(KB_NORM))
        from query_standards import validate_param, get_material_params
        results = []
        n3_params = get_material_params(material_id)
        for pn, pv in params.items():
            pc = None
            for p in n3_params:
                if p["参数名称"] == pn: pc = p["param_code"]; break
            if pc:
                r = validate_param(material_id, pc, pv)
                results.append({"param": pn, "value": pv, **r})
            else:
                results.append({"param": pn, "value": pv, "valid": None, "reason": "参数未定义"})
        return {"material_id": material_id, "results": results}
    except Exception as e:
        return {"material_id": material_id, "results": [], "error": str(e)}
