# -*- coding: utf-8 -*-
"""成套电气价格查找与估算函数。

本模块同时含用户提供的价格库、代码内置系数和经验估算。价格库来源/日期未在数据行中
验证；公式输出不是供应商报价、行业定额或工程认可的报价，正式报价前必须核实。
"""
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional, Tuple

# 报价口径全部来自 config/pricing.json（可用 EXTRACTOR_CONFIG_OVERRIDE_DIR 覆盖、可调铜价）。
# 出厂默认值只是量级参考，正式报价前必须按当日行情与供应商实价核实。
from .config import pricing_rules

_PRICING = pricing_rules()
_P_COPPER = _PRICING["copper"]
_P_RATES = _PRICING["rates"]

# 数据库路径：配置里显式给路径就用，否则用 backend/data/price_library.db
_DB_CFG = _PRICING.get("db") or {}
PRICE_DB_PATH = str(_DB_CFG.get("path") or (Path(__file__).resolve().parent.parent / "data" / "price_library.db"))

COPPER_PRICE_PER_KG = float(_P_COPPER["price_per_kg"])
COPPER_REFERENCE_PRICE_PER_TON = float(_P_COPPER.get("reference_price_per_ton", 72000))
COPPER_DENSITY = float(_P_COPPER["density_g_cm3"])

AUXILIARY_RATE = float(_P_RATES["auxiliary_rate"])
LABOR_OUTGOING_RATE = float(_P_RATES["labor_outgoing"])
LABOR_INCOMING_RATE = float(_P_RATES["labor_incoming"])
PROFIT_RATE_DEFAULT = float(_P_RATES.get("profit_rate", 0.08))
TAX_RATE_DEFAULT = float(_P_RATES.get("tax_rate", 0.13))
TEST_CERT_RATE = float(_P_RATES.get("test_cert_rate", 0.02))
DEFAULT_LIST_PRICE_RATIO = float(_P_RATES.get("default_list_price_ratio", 0.4))
AB_RATES = _P_RATES["ab"]

_ENCLOSURE_CFG = _PRICING.get("enclosure", {})
HEM_WH = float(_ENCLOSURE_CFG.get("hem_wh_mm", 50.0))
HEM_D = float(_ENCLOSURE_CFG.get("hem_d_mm", 20.0))
IP_PREMIUMS = dict(_ENCLOSURE_CFG.get("ip_premiums", {"IP55": 1.35, "IP65": 1.35, "IP44": 1.15}))
THICKNESS_DEFAULTS = dict(_ENCLOSURE_CFG.get("thickness_defaults", {"plastic": 1.2, "heavy_floor": 2.0, "small_box": 1.2, "default": 1.5}))
CIRCUIT_TIER_PRICES = list(_ENCLOSURE_CFG.get("circuit_tier_prices", []))

BRAND_ALIAS = dict(_PRICING["brand_alias"])
BRAND_TIERS = dict(_PRICING["brand_tiers"])
CATALOG_PRICE_BASE = {k: float(v) for k, v in _PRICING["catalog_price_base"].items() if not k.startswith("_")}
BUSBAR_SPECS = [tuple(row) for row in _PRICING["busbar_specs"]]
COPPER_STANDARD_SPECS = [tuple(row) for row in _PRICING["copper_standard_specs"]]
TYPE_A = dict(_PRICING["enclosure_type_a"])
CAT_SYNONYMS = {k: list(v) for k, v in _PRICING["category_synonyms"].items()}
if "GL" not in CAT_SYNONYMS and "ISOLATOR" in CAT_SYNONYMS:
    CAT_SYNONYMS["GL"] = CAT_SYNONYMS["ISOLATOR"]


def _cand_current_range(model_text: str) -> Tuple[Optional[int], Optional[int]]:
    """从候选型号中提取额定电流或范围 (low_a, high_a)。"""
    text = (model_text or "").upper()
    m_range = re.search(r"(\d{1,4})\s*[-~至]\s*(\d{1,4})\s*A", text)
    if m_range:
        return int(m_range.group(1)), int(m_range.group(2))
    m_ct = re.search(r"(?:^|[^\d])(\d{1,4})\s*/\s*[15]A?\b", text)
    if m_ct:
        v = int(m_ct.group(1))
        return v, v
    m_a = re.search(r"(?:\b|[CD/_-])([1-9]\d{0,3})\s*A\b", text)
    if m_a:
        v = int(m_a.group(1))
        return v, v
    m_tm = re.search(r"\bTM[A-Z]*([1-9]\d{0,3})(?:[-/DA\s]|\b)", text)
    if m_tm:
        v = int(m_tm.group(1))
        return v, v
    m_cd = re.search(r"(?:^|[\s/_-])[CD]([1-9]\d{0,2})(?:A|\b)", text)
    if m_cd:
        v = int(m_cd.group(1))
        return v, v
    return None, None


def _cand_frame(model_text: str) -> Optional[int]:
    """从候选型号中提取壳架等级电流 (frame_a)。"""
    text = (model_text or "").upper()
    m_abb = re.search(r"\b(?:XT[1-4][A-Z]*|A[1-3][A-Z]*)-?(\d{2,4})\b", text)
    if m_abb:
        try:
            return int(m_abb.group(1))
        except Exception:
            pass
    m = re.search(r"(?:NXMH|CDM\w*?|NM\d*|NSX|CVS|EZD|GL|NXB|C65|EZ7|DPN|RMM\d*|NH40|HD\d*|HH\d*|HR\d*)[-_]?(\d{2,4})", text)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    return None


def _cand_poles(model_text: str) -> Optional[str]:
    """从候选型号中提取极数 (1P, 2P, 3P, 4P, 1P+N, 3P+N)。"""
    text = (model_text or "").upper()
    m_pn = re.search(r"\b([1-3])P\+N\b", text)
    if m_pn:
        return f"{m_pn.group(1)}P+N"
    m_p = re.search(r"\b([1-4])P(?:[1-4]D)?\b", text)
    if m_p:
        return f"{m_p.group(1)}P"
    m_rel = re.search(r"/([1-4])\d{3,4}\b", text)
    if m_rel:
        return f"{m_rel.group(1)}P"
    for p_num, p_word in [("1", "1极"), ("1", "单极"), ("2", "2极"), ("2", "二极"),
                          ("3", "3极"), ("3", "三极"), ("4", "4极"), ("4", "四极")]:
        if p_word in text:
            return f"{p_num}P"
    return None


def _cand_is_residential(model_text: str, cat_text: str) -> bool:
    """判定是否为住宅/建筑专供限定用途物料。"""
    comb = (str(model_text or "") + " " + str(cat_text or "")).upper()
    return any(k in comb for k in ("住宅专供", "建筑专供", "专供"))

# 兜底估算公式与开关：默认关闭。按公式推出来的金额不是供应商报价，不得写进报价单。
_PRICING_GATES = _PRICING.get("gates") or {}
ALLOW_FORMULA_FALLBACK_PRICING = bool(_PRICING_GATES.get("allow_formula_fallback_pricing", False))
UNKNOWN_PRICE_BASIS = str(_PRICING_GATES.get("unknown_price_basis") or "价格库未收录该规格（待人工询价）")
FORMULA_PRICE_BASIS_PREFIX = str(_PRICING_GATES.get("formula_price_basis_prefix") or "规则估算（待核）")
FALLBACK_FORMULAS = _PRICING.get("fallback_formulas") or {}
# 候选打分权重直接决定采用哪条价格记录，因此也必须可配置（改权重前先用真实图纸复核）
MATCH_SCORING = _PRICING.get("match_scoring") or {}
CURRENT_PARSE_RE = _PRICING.get("current_parse_regex") or r"(?:/|C|D|M|-)?(\d{1,4})A"
CURRENT_PARSE_FALLBACK_RE = _PRICING.get("current_parse_fallback_regex") or r"[CD](\d{1,3})"
_BRANDS = _PRICING.get("brands") or {}
DEFAULTS = _PRICING.get("defaults") or {}
REFERENCE_BRAND = str(_BRANDS.get("reference_brand") or "施耐德")
DEFAULT_BRAND = str(_BRANDS.get("default_brand") or "正泰")
JOINT_VENTURE_BRANDS = tuple(_BRANDS.get("joint_venture") or ())
COMPARISON_BRANDS = tuple(_BRANDS.get("comparison_brands") or ())
BENCHMARK_BRAND = str(_BRANDS.get("benchmark_brand") or REFERENCE_BRAND)
DOMESTIC_SUBSTITUTE = str(_BRANDS.get("domestic_substitute") or DEFAULT_BRAND)
CANDIDATE_LOOKUP_BRANDS = tuple(_BRANDS.get("candidate_lookup") or ())
INFO_PRICE_ORDER = tuple(_BRANDS.get("info_price_order") or ())
MARKET_PRICE_ORDER = tuple(_BRANDS.get("market_price_order") or ())
_REF_EST = _PRICING["reference_estimates"]
REFERENCE_ESTIMATE_SOURCE = str(_REF_EST["source"])
REFERENCE_ESTIMATE_BASIS = str(_REF_EST["basis"])
UNKNOWN_MATERIAL_BASIS = str(_PRICING_GATES.get("unknown_material_basis") or "材料/板厚未收录（待人工询价）")
STANDARD_CURRENTS = [float(c) for c in (_PRICING.get("standard_currents") or [])]


def _formula_base_price(cat: str, poles: str, matched_curr: float) -> Optional[float]:
    """按配置公式推面价。仅在 gates.allow_formula_fallback_pricing 打开时使用。"""
    if not ALLOW_FORMULA_FALLBACK_PRICING:
        return None
    expr = (FALLBACK_FORMULAS.get("base") or {}).get(cat) or FALLBACK_FORMULAS.get("default_base")
    if not expr:
        return None
    poles_num = int(poles[0]) if poles and poles[0].isdigit() else 1
    try:
        return float(eval(expr, {"__builtins__": {}}, {"poles_num": poles_num,
                                                       "curr": matched_curr,
                                                       "matched_curr": matched_curr}))
    except Exception as exc:  # noqa: BLE001 - 配置写错不能拖垮报价
        print(f"[pricing] 兜底公式求值失败 ({cat}): {exc!r}")
        return None

# 钣金材料价用 "材料|厚度" 组合键存储，避免 JSON 不支持元组键
MATERIAL_PRICES = {}
for combo, value in _PRICING["material_prices"].items():
    if combo.startswith("_"):
        continue
    if "|" in combo:
        name, thickness = combo.rsplit("|", 1)
        MATERIAL_PRICES[(name, float(thickness))] = float(value)
    else:
        MATERIAL_PRICES[combo] = float(value)


def norm_brand(b: Any) -> str:
    """将任意品牌写法归一化为价格库规范品牌名"""
    if not b:
        return ""
    s = str(b).strip()
    return BRAND_ALIAS.get(s.lower(), BRAND_ALIAS.get(s, s))


def norm_str(s: Any) -> str:
    """去除空白、连字符并大写化"""
    return re.sub(r"[\s\-_/]+", "", str(s or "")).upper()


def get_db_connection() -> Optional[sqlite3.Connection]:
    """获取 SQLite 价格库连接"""
    if not os.path.exists(PRICE_DB_PATH):
        return None
    try:
        conn = sqlite3.connect(f"file:{PRICE_DB_PATH}?mode=ro", uri=True)
        return conn
    except Exception:
        try:
            return sqlite3.connect(PRICE_DB_PATH)
        except Exception:
            return None


def get_price_library_stats() -> dict:
    """获取价格库全库统计信息 (总条数、品牌列表与条数)"""
    conn = get_db_connection()
    if not conn:
        return {"total_count": 0, "brands": {}, "available": False}
    try:
        c = conn.cursor()
        c.execute("SELECT count(*) FROM price_library")
        total = c.fetchone()[0]
        c.execute("SELECT brand_norm, count(*) FROM price_library GROUP BY brand_norm ORDER BY count(*) DESC")
        brands = {row[0]: row[1] for row in c.fetchall()}
        return {"total_count": total, "brands": brands, "available": True}
    finally:
        conn.close()


def _parse_candidate_features(model: str, category: str = "") -> dict:
    """提取价格库候选型号的电气参数（极数、壳架电流、额定电流/电流范围、是否专供）"""
    txt = (model or "").upper()
    cat = (category or "").upper()

    # 1. 极数
    poles = None
    m_p = re.search(r"\b([1-4]P(?:\+N|[1-4]D)?)\b", txt)
    if m_p:
        poles = m_p.group(1)
    elif (m_pole := re.search(r"\b([1-4])极\b", txt)):
        poles = f"{m_pole.group(1)}P"
    elif (m_pole := re.search(r"/(3\d{3}|4\d{3})\b", txt)):
        poles = f"{m_pole.group(1)[0]}P"
    elif (m_pole := re.search(r"/([1-4])(?:[A-Z]|\b)", txt)):
        poles = f"{m_pole.group(1)}P"

    # 2. 壳架电流 frame
    frame = None
    m_fr = re.search(r"(?:MCCB|GL|HGL|NH40|NM[A-Z0-9]*|NXM[A-Z0-9]*|CVS|NSX|CDM[A-Z0-9]*|RMM[A-Z0-9]*|NDM[A-Z0-9]*|3VA[A-Z0-9]*|XT[1-4]?[A-Z]?|HD[A-Z0-9]*|HH[A-Z0-9]*|HR[A-Z0-9]*)-?(\d{2,4})", txt)
    if m_fr:
        frame = int(m_fr.group(1))

    # 3. 额定电流或电流范围
    c_min = None
    c_max = None
    m_rng = re.search(r"\b(\d+)\s*[-~至到]\s*(\d+)\s*A\b", txt)
    if m_rng:
        c_min, c_max = int(m_rng.group(1)), int(m_rng.group(2))
    else:
        m_ct = re.search(r"(?:^|[^\d])(\d+)/[15]A?\b", txt)
        if m_ct:
            c_min = c_max = int(m_ct.group(1))
        else:
            m_a = re.search(r"(?:^|[\s/_\-CD])([1-9]\d{0,3})\s*A(?=[^A-Za-z0-9]|$)", txt)
            if m_a:
                c_min = c_max = int(m_a.group(1))
            else:
                m_tm = re.search(r"\bTM[A-Z]*([1-9]\d{0,3})(?:[-/DA\s]|$)", txt)
                if m_tm:
                    c_min = c_max = int(m_tm.group(1))
                else:
                    m_cd = re.search(r"(?:^|[\s/_-])[CD]([1-9]\d{0,2})(?:A|\b)", txt)
                    if m_cd:
                        c_min = c_max = int(m_cd.group(1))
                    elif frame is not None and "隔离开关" in cat:
                        c_min = c_max = frame

    is_special = any(k in txt or k in cat for k in ("住宅专供", "建筑专供", "专供", "家装"))

    return {
        "poles": poles,
        "frame": frame,
        "curr_min": c_min,
        "curr_max": c_max,
        "is_special": is_special,
    }


def lookup_price_library(
    cat_code: str,
    brand: str,
    poles: str = "",
    curr_a: Optional[int] = None,
    raw_spec: str = "",
    curve: str = "C",
    breaking_ka: int = 6,
    rel_code: str = "",
    frame_a: Optional[int] = None,
) -> Optional[dict]:
    """在 5.1 万条价格库中精确/特征匹配元器件，返回含税采购价与详情"""
    conn = get_db_connection()
    if not conn:
        return None

    b_norm = norm_brand(brand)
    c = conn.cursor()
    try:
        # 1. 精确型号对标 (若图纸已给出明确型号，如 NXB-63 或 EZ7)
        if raw_spec:
            spec_norm = norm_str(raw_spec)
            c.execute("""
                SELECT code, brand, category, model, price_tax, list_price, discount, remark
                FROM price_library
                WHERE brand_norm=? AND model_norm=?
                LIMIT 1
            """, (b_norm, spec_norm))
            r = c.fetchone()
            if r and r[4] and r[4] > 0:
                return {
                    "code": r[0], "brand": r[1], "category": r[2], "model": r[3],
                    "price_tax": float(r[4]), "list_price": float(r[5] or 0),
                    "discount": float(r[6] or 0), "match_status": "exact",
                }

        # 2. 类别与参数特征匹配门禁
        if cat_code != "SPD" and (curr_a is None or curr_a <= 0):
            return None

        synonyms = CAT_SYNONYMS.get(cat_code, [cat_code])
        cat_clause = " OR ".join(["category LIKE ?" for _ in synonyms])
        cat_params = [f"%{s}%" for s in synonyms]

        # 自动提取脱扣器代号 (如 3300, 3200, 4300, 4200)
        if not rel_code and raw_spec:
            m_rel = re.search(r"\b(3300|3200|4300|4200|3320|4320)\b", raw_spec)
            if m_rel:
                rel_code = m_rel.group(1)

        pole_digits = poles[0] if poles and poles[0].isdigit() else ""
        if cat_code == "CT":
            poles = ""
            pole_digits = ""
            pole_patterns = ["%"]
        else:
            pole_patterns = [f"%{poles}%"] if poles else ["%"]
            if pole_digits:
                pole_patterns.extend([f"%{pole_digits}极%", f"%{pole_digits}P%"])
                if cat_code in ("ISOLATOR", "MCCB", "ATS"):
                    pole_patterns.append(f"%/{pole_digits}%")
            if cat_code == "RCBO" and "1P+N" in poles:
                pole_patterns.extend(["%1P+N%", "%1P%", "%2P%", "%2极%"])
            pole_patterns = list(dict.fromkeys(pole_patterns))

        curr_patterns = []
        if cat_code == "SPD":
            curr_patterns = ["%"]
        elif cat_code == "CT" and curr_a:
            curr_patterns = [f"%{curr_a}/5%"]
        elif curr_a:
            curr_patterns = [
                f"%{curr_a}A%",
                f"%C{curr_a}%",
                f"%D{curr_a}%",
                f"%/{curr_a}%",
                f"%{curr_a}/%",
                f"% {curr_a}%",
                f"%{curr_a}-%",
                f"%{curr_a}~%",
            ]
            if frame_a:
                curr_patterns.append(f"%{frame_a}%")
        elif frame_a:
            curr_patterns = [f"%{frame_a}%"]
        else:
            curr_patterns = ["%"]

        query = f"""
            SELECT code, brand, category, model, price_tax, list_price, discount, remark
            FROM price_library
            WHERE brand_norm=?
              AND ({cat_clause})
              AND category NOT LIKE '零部件%' AND (category LIKE '%漏电%' OR category NOT LIKE '%附件%')
              AND price_tax > 0
        """

        candidates = []
        seen_codes = set()
        for cp in curr_patterns:
            for pp in pole_patterns:
                params = [b_norm] + cat_params + [pp, cp]
                sub_q = query + " AND model_norm LIKE ? AND model_norm LIKE ? LIMIT 40"
                c.execute(sub_q, params)
                for row in c.fetchall():
                    if row[0] not in seen_codes:
                        seen_codes.add(row[0])
                        candidates.append({
                            "code": row[0], "brand": row[1], "category": row[2], "model": row[3],
                            "price_tax": float(row[4]), "list_price": float(row[5] or 0),
                            "discount": float(row[6] or 0), "remark": row[7] or "",
                        })

        # 若带 P 条件候选不足，对于带数字极数的 MCCB / ATS (如 /3300, /4SZ) 扩展匹配
        if (not candidates or len(candidates) < 3) and pole_digits and cat_code in ("ATS", "MCCB"):
            for cp in curr_patterns:
                p_like = f"%/{pole_digits}%"
                params = [b_norm] + cat_params + [p_like, cp]
                sub_q = query + " AND model_norm LIKE ? AND model_norm LIKE ? LIMIT 40"
                c.execute(sub_q, params)
                for row in c.fetchall():
                    if row[0] not in seen_codes:
                        seen_codes.add(row[0])
                        candidates.append({
                            "code": row[0], "brand": row[1], "category": row[2], "model": row[3],
                            "price_tax": float(row[4]), "list_price": float(row[5] or 0),
                            "discount": float(row[6] or 0), "remark": row[7] or "",
                        })

        if not candidates:
            return None

        # 硬约束门禁校验 (Hard Constraints Filtering)
        valid_candidates = []
        for cand in candidates:
            cf = _parse_candidate_features(cand["model"], cand["category"])

            # 1. 品类一致：category 必须在 allowed_cats 内
            if not any(s in cand["category"] for s in synonyms):
                continue

            # 2. 极数一致：若图纸给出极数且候选可解析极数，必须完全一致
            if poles and cf["poles"] and cf["poles"] != poles:
                if cat_code == "RCBO" and {poles, cf["poles"]} == {"1P+N", "2P"}:
                    pass
                else:
                    continue

            # 3. 额定电流落在候选范围内
            if curr_a is not None and cat_code != "SPD":
                if cat_code == "ISOLATOR":
                    cand_cap = cf["curr_max"] or cf["frame"]
                    if cand_cap is not None and cand_cap < curr_a:
                        continue
                elif cat_code == "CT":
                    if cf["curr_min"] is not None and cf["curr_min"] != curr_a:
                        continue
                else:
                    if cf["curr_min"] is not None and cf["curr_max"] is not None:
                        if not (cf["curr_min"] <= curr_a <= cf["curr_max"]):
                            continue
                    else:
                        continue

            # 4. 壳架电流 >= 设计值 (有标注时)
            if frame_a is not None and cf["frame"] is not None:
                if cf["frame"] < frame_a:
                    continue

            # 5. 分断能力硬门禁
            if breaking_ka > 6:
                txt = (cand["model"] + " " + cand["category"] + " " + cand.get("remark", "")).upper()
                m_ka = re.search(r"(\d{1,3})\s*KA\b", txt)
                if m_ka:
                    cand_ka = int(m_ka.group(1))
                elif cat_code == "MCCB":
                    cand_ka = 50 if ("高分断" in txt or re.search(r"(?:-|\b)[A-Z0-9]*H\b", txt)) else 35
                elif cat_code == "MCB":
                    cand_ka = 10 if ("高分断" in txt or re.search(r"(?:-|\b)[A-Z0-9]*H\b", txt)) else 6
                else:
                    cand_ka = 6
                if cand_ka < breaking_ka:
                    continue

            valid_candidates.append((cand, cf))

        if not valid_candidates:
            return None

        # 6. “住宅专供”限定用途约束：标准工程集采优先于住宅专供
        non_special = [item for item in valid_candidates if not item[1]["is_special"]]
        has_only_special = False
        if non_special:
            filtered = non_special
        else:
            # 无其他候选，且标有专供
            filtered = valid_candidates
            has_only_special = True

        # 满足约束后排序：壳架最接近、同系列、来源可靠优先
        def score_candidate(item: Tuple[dict, dict]) -> Tuple[float, float]:
            cand, cf = item
            model_text = cand["model"].upper()
            s = 0.0

            if not cf["is_special"]:
                s += 50.0

            # 壳架最接近
            if frame_a:
                if cf["frame"]:
                    s -= abs(cf["frame"] - frame_a) * 0.5
                    if cf["frame"] == frame_a:
                        s += 30.0
                else:
                    s -= 20.0

            # 隔离开关优先标准负荷隔离开关（如 NH40）
            if cat_code == "ISOLATOR" and "NH40" in model_text:
                s += 25.0

            # 额定电流精确吻合
            if curr_a and cf["curr_min"] == curr_a and cf["curr_max"] == curr_a:
                s += 25.0

            # 脱扣器代号匹配 (如 3300)
            if rel_code and rel_code in model_text:
                s += 20.0

            # 脱扣曲线
            if curve in model_text:
                s += 5.0

            return (s, -cand["price_tax"])

        filtered.sort(key=score_candidate, reverse=True)
        best_cand, best_cf = filtered[0]
        best = dict(best_cand)
        best["match_status"] = "parameter-matched"
        best["only_special"] = has_only_special
        return best
    finally:
        conn.close()


def parse_component_features(raw_spec: str, category: str = "") -> dict:
    """从元器件规格字符串中提取特征向量 (品类、极数、额定电流、壳架电流、分断能力、脱扣特性、脱扣器代号)"""
    text = (raw_spec or "").upper().replace(" ", "")
    raw_upper = (raw_spec or "").upper().strip()
    spaced = re.sub(r"\s+", "/", raw_upper)

    # 1. 自动判定大类
    cat_upper = (category or "").upper()
    cat_code = "OTHER"
    has_leakage = False

    if any(k in text or k in cat_upper for k in ("SPD", "浪涌", "电涌", "避雷", "防雷")):
        cat_code = "SPD"
    elif any(k in text or k in cat_upper for k in ("CT", "互感器")) or re.search(r"(?:^|[^\d])\d+/5A?\b", raw_upper):
        cat_code = "CT"
    elif any(k in text or k in cat_upper for k in ("GL-", "HGL", "NH40", "隔离开关", "负荷隔离开关", "负荷开关", "刀开关", "隔离器")):
        cat_code = "ISOLATOR"
    elif any(k in text or k in cat_upper for k in ("ATS", "双电源", "ATSE", "自动转换开关", "双源开关")):
        cat_code = "ATS"
    elif any(k in text or k in cat_upper for k in ("RCBO", "LE", "VM", "漏电", "残余电流", "VIGI")):
        cat_code = "RCBO"
        has_leakage = True
    elif any(k in text or k in cat_upper for k in ("MCCB", "NM", "NSX", "CVS", "塑壳", "CDM", "3VA", "XT")):
        cat_code = "MCCB"
    elif any(k in text or k in cat_upper for k in ("MCB", "NXB", "IC65", "DZ47", "微断", "微型断路器", "小型断路器")) or re.search(r"^[CD]\d+", text) or re.search(r"/[CD]\d+", text):
        cat_code = "MCB"
    elif any(k in text or k in cat_upper for k in ("KM", "接触器", "CJX2", "LC1")):
        cat_code = "KM"
    elif category:
        cat_code = category.upper()

    # 2. 识别极数
    poles = "" if cat_code == "CT" else "1P"
    poles_m = re.search(r"\b([1-4])P(?:\+N|[1-4]D)?\b", raw_upper)
    if poles_m:
        poles = f"{poles_m.group(1)}P"
    elif (m_pole := re.search(r"\b([1-4])极\b", raw_upper)):
        poles = f"{m_pole.group(1)}P"
    elif (m_pole := re.search(r"/(3\d{3}|4\d{3})\b", raw_upper)):
        poles = f"{m_pole.group(1)[0]}P"
    elif cat_code in ("MCCB", "ISOLATOR", "KM"):
        poles = "3P"
    elif cat_code in ("ATS", "SPD"):
        poles = "4P"
    elif cat_code == "RCBO":
        poles = "2P"
    elif cat_code == "CT":
        poles = ""

    # 3. 识别壳架电流 frame_a (Inm) 与 额定电流 current_a (In)
    frame_val = None
    current_val = None

    # 双数字模式：如 MCCB-160MA/50A/3P, GL-250/200A/3P, NM1-125S/3300 100A
    m_dual = re.search(r"(?:MCCB|GL|HGL|NH40|NM[A-Z0-9]*|NXM[A-Z0-9]*|CVS|NSX|CDM[A-Z0-9]*|RMM[A-Z0-9]*|NDM[A-Z0-9]*|HD[A-Z0-9]*|HH[A-Z0-9]*|HR[A-Z0-9]*)-?(\d{2,4})[A-Z]*/(?:[A-Z0-9]*/)?(\d{1,4})A", raw_upper)
    if m_dual:
        f_cand, c_cand = int(m_dual.group(1)), int(m_dual.group(2))
        if f_cand >= c_cand:
            frame_val, current_val = f_cand, c_cand

    if frame_val is None:
        m_fr = re.search(r"(?:MCCB|GL|HGL|NH40|NM[A-Z0-9]*|NXM[A-Z0-9]*|CVS|NSX|CDM[A-Z0-9]*|RMM[A-Z0-9]*|NDM[A-Z0-9]*|3VA[A-Z0-9]*|XT|HD[A-Z0-9]*|HH[A-Z0-9]*|HR[A-Z0-9]*)-?(\d{2,4})", raw_upper)
        if m_fr:
            frame_val = int(m_fr.group(1))

    if current_val is None:
        if cat_code == "CT":
            m_ct = re.search(r"(?:^|[^\d])(\d+)/5A?\b", raw_upper)
            if m_ct:
                current_val = int(m_ct.group(1))
        elif cat_code == "SPD":
            current_val = None
        else:
            curr_m = re.search(r"(?:^|[/CD\s\-])([1-9]\d{0,3})\s*A\b", spaced)
            if curr_m:
                current_val = int(curr_m.group(1))
            else:
                num_m = re.search(CURRENT_PARSE_FALLBACK_RE, spaced)
                if num_m:
                    current_val = int(num_m.group(1))
                elif frame_val is not None and cat_code == "ISOLATOR":
                    current_val = frame_val

    # 4. 识别塑壳脱扣器代号 (如 /3300, /3200, /4300, TM)
    rel_code = ""
    rm = re.search(r"/(3300|3200|4300|4200|3320|4320)", raw_upper)
    if rm:
        rel_code = rm.group(1)
    elif "TM" in raw_upper and cat_code == "MCCB":
        rel_code = "3300" if poles == "3P" else ("4300" if poles == "4P" else "")

    # 5. 识别分断能力 (如 6kA, 10kA, 35kA, 50kA)
    breaking_ka = 6
    ka_m = re.search(r"(\d{1,3})\s*KA", raw_upper)
    if ka_m:
        breaking_ka = int(ka_m.group(1))
    elif cat_code == "MCCB":
        breaking_ka = 35
    elif cat_code == "MCB":
        breaking_ka = 6

    # 6. 脱扣曲线
    curve = "C"
    if "D" in text and ("D16" in text or "D20" in text or "D32" in text or "D型" in text or "动力" in text):
        curve = "D"

    return {
        "raw": raw_spec,
        "category": cat_code,
        "poles": poles,
        "current_a": current_val,
        "frame_a": frame_val,
        "breaking_ka": breaking_ka,
        "curve": curve,
        "has_leakage": has_leakage,
        "rel_code": rel_code,
    }


def calculate_component_unit_price(
    raw_spec: str,
    brand: str = "",
    category: str = ""
) -> Tuple[float, float, str]:
    """计算单个元器件的 (实际采购单价, 目录面价, 计价判定依据)

    价格库记录与内置规则需区分展示。内置规则属于估算，不构成供应商报价或采购依据。
    """
    brand = (brand or REFERENCE_BRAND).strip() or REFERENCE_BRAND
    if not raw_spec or str(raw_spec).strip() in ("-", "待确认", "待定"):
        return 0.0, 0.0, "规格参数缺失（待确认）"

    feats = parse_component_features(raw_spec, category)
    cat = feats["category"]
    poles = feats["poles"]
    curr = feats["current_a"]
    frame_a = feats.get("frame_a")

    # 规格未标注额定电流时，如实标疑，严禁凭空编造（SPD 等无额定工作电流品类除外）
    if curr is None and cat != "SPD":
        return 0.0, 0.0, "规格电流缺失（待核）"

    # 一级尝试：查询本地导入的价格记录；来源和有效期需在报价时另行核验。
    matched_lib = lookup_price_library(
        cat,
        brand,
        poles=poles,
        curr_a=curr,
        raw_spec=raw_spec,
        curve=feats.get("curve", "C"),
        breaking_ka=feats.get("breaking_ka", 6),
        rel_code=feats.get("rel_code", ""),
        frame_a=frame_a,
    )
    if matched_lib:
        cost_p = round(matched_lib["price_tax"], 2)
        list_p = round(matched_lib["list_price"], 2) if matched_lib["list_price"] > 0 else round(cost_p / DEFAULT_LIST_PRICE_RATIO, 2)
        code = matched_lib["code"]
        b_name = matched_lib["brand"]
        m_name = matched_lib["model"]
        sp_note = " (注：仅有住宅专供候选，工业柜需人工确认)" if matched_lib.get("only_special") else ""
        basis = f"价格库匹配[{code}] {b_name} {m_name} 含税￥{cost_p:.2f} (面价￥{list_p:.2f}){sp_note}"
        return cost_p, list_p, basis

    # 二级回退：标准面价知识库（CATALOG_PRICE_BASE）。命中就是命中，未命中不再编造金额。
    if curr is None:
        return 0.0, 0.0, UNKNOWN_PRICE_BASIS
    standard_currs = STANDARD_CURRENTS or [10, 16, 20, 25, 32, 40, 50, 63, 80, 100, 125, 160, 200, 250, 400, 630]
    matched_curr = min(standard_currs, key=lambda x: abs(x - curr))

    key = f"{cat}-{poles}-{int(matched_curr)}A"
    list_price = CATALOG_PRICE_BASE.get(key)
    exact_match = list_price is not None
    estimated = False

    if not list_price:
        list_price = _formula_base_price(cat, poles, float(matched_curr))
        estimated = list_price is not None
    if not list_price:
        # 价格库与标准面价库都没这档：如实返回待询价，别用公式凑一个数字出来冒充报价。
        # 若项目确实接受估算，在 config/pricing.json 打开 gates.allow_formula_fallback_pricing。
        return 0.0, 0.0, UNKNOWN_PRICE_BASIS

    tier_info = BRAND_TIERS.get(norm_brand(brand), BRAND_TIERS.get(REFERENCE_BRAND, {}))
    if cat in ("MCB", "RCBO"):
        discount = tier_info.get("mcb_discount", 0.45)
    elif cat == "MCCB":
        discount = tier_info.get("mccb_discount", 0.42)
    elif cat == "ATS":
        discount = tier_info.get("ats_discount", 0.48)
    else:
        discount = (tier_info.get("mcb_discount", 0.45) + tier_info.get("mccb_discount", 0.42)) / 2.0

    multiplier = tier_info.get("multiplier", 1.0)
    cost_price = round(list_price * discount * multiplier, 2)
    if exact_match:
        basis = f"标准库匹配[{key}] 面价￥{list_price} 折扣率{discount:.2f}"
    elif estimated:
        basis = f"{FORMULA_PRICE_BASIS_PREFIX}[{key}] 面价￥{list_price} 折扣率{discount:.2f}"
    return cost_price, round(list_price, 2), basis


def parse_box_dimensions(size_str: Any) -> Optional[Tuple[float, float, float]]:
    """从尺寸规格字符串（如 '800*600*200' / '600x800x250' / 'GGD 800×2200×600'）中提取 (宽, 高, 深) 毫米数"""
    if not size_str:
        return None
    m = re.search(r"(\d{2,4})\s*[*xX×]\s*(\d{2,4})\s*[*xX×]\s*(\d{2,4})", str(size_str))
    if m:
        nums = [float(m.group(1)), float(m.group(2)), float(m.group(3))]
        depth = min(nums)
        remaining = [n for n in nums if n != depth]
        if len(remaining) < 2:
            remaining = [nums[0], nums[1]]
        width, height = remaining[0], remaining[1]
        return width, height, depth
    return None


def calc_enclosure_unfolding(
    width: float,
    height: float,
    depth: float,
    box_type: str = "安装板",
    material: str = "冷轧钢板",
    thickness: float = 1.5,
    door_material: Optional[str] = None,
    door_thickness: Optional[float] = None,
    A: Optional[float] = None,
    cover_panel_m2: float = 0.0,
    cover_panel_price: float = 0.0
) -> dict:
    """非标配电箱箱体价格计算（基于物理展开面积法）

    公式：
      底面面积 = (宽+50折边) × (高+50折边) × 底面系数A       （mm² × A）
      四周面积 = ((高+50折边) + (宽+50折边)) × (深+20折边) × 2 （mm²）
      门面积   = (宽+50折边) × (高+50折边)                   （mm²）
      箱体价格 = (底面面积 + 四周面积) × 材料加工单价 + 门面积 × 门加工单价
      封板价格 = 封板面积(m²) × 封板材料单价
      总价     = 底面价 + 四周价 + 门价 + 封板价
    """
    hem_wh = HEM_WH  # 宽/高折边 mm，来自 config/pricing.json
    hem_d = HEM_D   # 深度折边 mm，来自 config/pricing.json
    coef_a = A if A is not None else TYPE_A.get(box_type, 2.0)

    # 查取材料加工单价 (元/m²)
    mat_price = MATERIAL_PRICES.get((material, thickness))
    if mat_price is None:
        # 材料/板厚不在价格表里：不得拿某个固定单价顶上，如实标疑待询价
        return {
            "width_mm": width, "height_mm": height, "depth_mm": depth, "A": coef_a,
            "bottom_area_m2": 0.0, "side_area_m2": 0.0, "door_area_m2": 0.0,
            "total_area_m2": 0.0, "bottom_price": 0.0, "side_price": 0.0,
            "door_price": 0.0, "cover_price": 0.0, "total_price": 0.0,
            "desc": f"{material}{thickness}mm {UNKNOWN_MATERIAL_BASIS}", "price_known": False,
        }
    door_mat = door_material or material
    door_thk = door_thickness if door_thickness is not None else thickness
    door_price = MATERIAL_PRICES.get((door_mat, door_thk), mat_price)

    bottom_area_mm2 = (width + hem_wh) * (height + hem_wh) * coef_a
    side_area_mm2 = ((height + hem_wh) + (width + hem_wh)) * (depth + hem_d) * 2.0
    door_area_mm2 = (width + hem_wh) * (height + hem_wh)

    bottom_area_m2 = bottom_area_mm2 / 1_000_000.0
    side_area_m2 = side_area_mm2 / 1_000_000.0
    door_area_m2 = door_area_mm2 / 1_000_000.0
    total_area_m2 = bottom_area_m2 + side_area_m2 + door_area_m2

    bottom_price = round(bottom_area_m2 * mat_price, 2)
    side_price = round(side_area_m2 * mat_price, 2)
    door_price_total = round(door_area_m2 * door_price, 2)
    cover_price = round(cover_panel_m2 * cover_panel_price, 2)
    total_price = round(bottom_price + side_price + door_price_total + cover_price, 2)

    desc = f"钣金展开法 [{int(width)}x{int(height)}x{int(depth)} {material}{thickness}mm 结构:{box_type} A={coef_a} 展面{total_area_m2:.2f}㎡]"
    return {
        "width_mm": width,
        "height_mm": height,
        "depth_mm": depth,
        "A": coef_a,
        "bottom_area_m2": round(bottom_area_m2, 4),
        "side_area_m2": round(side_area_m2, 4),
        "door_area_m2": round(door_area_m2, 4),
        "total_area_m2": round(total_area_m2, 4),
        "bottom_price": bottom_price,
        "side_price": side_price,
        "door_price": door_price_total,
        "cover_price": cover_price,
        "total_price": total_price,
        "desc": desc,
        "price_known": True,
    }


def resolve_box_enclosure_specs(box: dict, w: float, h: float, d: float, is_floor: bool) -> Tuple[str, str, float]:
    """从配电箱/柜体元数据与上下文动态解析钣金外壳结构类型 (b_type)、材质 (material) 与板厚 (thickness)。
    彻底消除写死“安装板”和写死“冷轧钢板”，支持图纸中真实标注的立板、横梁、不锈钢、双门、防护等结构要求。
    """
    raw_texts = [
        str(box.get("structure") or ""),
        str(box.get("box_type") or ""),
        str(box.get("type") or ""),
        str(box.get("install_type") or box.get("install") or ""),
        str(box.get("note") or ""),
        str(box.get("name") or box.get("box_name") or ""),
        str(box.get("material") or ""),
    ]
    combined = " ".join(raw_texts).strip()

    # 1. 材质解析 (Material)
    mat_explicit = str(box.get("material") or "").strip()
    if mat_explicit and any(m in mat_explicit for m in ("冷轧", "不锈钢", "塑料", "PC")):
        if "304" in mat_explicit:
            material = "304#不锈钢板"
        elif "201" in mat_explicit:
            material = "201#不锈钢板"
        elif "不锈钢" in mat_explicit:
            material = "304#不锈钢板"
        elif "塑料" in mat_explicit or "PC" in mat_explicit:
            material = "塑料面板（PC料，阻燃）"
        else:
            material = "冷轧钢板"
    elif "304" in combined:
        material = "304#不锈钢板"
    elif "201" in combined:
        material = "201#不锈钢板"
    elif "不锈钢" in combined:
        material = "304#不锈钢板"
    elif "塑料" in combined or "PC料" in combined or "PC面板" in combined:
        material = "塑料面板（PC料，阻燃）"
    else:
        material = "冷轧钢板"

    # 2. 板厚解析 (Thickness)
    thk_explicit = box.get("thickness")
    thickness = None
    if thk_explicit is not None:
        try:
            val = float(thk_explicit)
            if val > 0:
                thickness = val
        except (ValueError, TypeError):
            pass

    if thickness is None:
        m_thk = re.search(r"(?:厚度|δ|t|厚)\s*[:=]?\s*([0-2](?:\.\d+)?)\s*mm?", combined, re.IGNORECASE)
        if not m_thk:
            m_thk = re.search(r"\b([12]\.[025])\s*mm\b", combined, re.IGNORECASE)
        if m_thk:
            try:
                thickness = float(m_thk.group(1))
            except ValueError:
                pass

    if thickness is None:
        if material == "塑料面板（PC料，阻燃）":
            thickness = float(THICKNESS_DEFAULTS.get("plastic", 1.2))
        elif is_floor or max(w, h) >= 1600:
            thickness = float(THICKNESS_DEFAULTS.get("heavy_floor", 2.0))
        elif max(w, h) <= 450:
            thickness = float(THICKNESS_DEFAULTS.get("small_box", 1.2))
        else:
            thickness = float(THICKNESS_DEFAULTS.get("default", 1.5))

    # 3. 动态解析箱体/柜体内部钣金结构 (b_type)
    # 彻底杜绝写死“安装板”：支持立板、立板+支架、安装立板、双门、横梁、空箱等通用工业结构
    struct_explicit = str(box.get("structure") or "").strip()
    if struct_explicit and struct_explicit in TYPE_A:
        b_type = struct_explicit
    elif re.search(r"立板.*支架.*封板", combined):
        b_type = "立板+支架+封板"
    elif re.search(r"立板.*支架", combined):
        b_type = "立板+支架"
    elif re.search(r"立板.*横梁", combined):
        b_type = "立板+横梁"
    elif re.search(r"立板.*封板", combined):
        b_type = "立板+封板"
    elif "立板" in combined or "安装立板" in combined:
        b_type = "立板"
    elif "安装板+支架+封板+帽子" in combined or ("安装板+支架+封板" in combined and ("防雨" in combined or "帽子" in combined)):
        b_type = "安装板+支架+封板+帽子"
    elif "安装板+支架+封板" in combined or "安装版+支架+封板" in combined:
        b_type = "安装板+支架+封板"
    elif "安装板+帽子" in combined or "安装版+帽子" in combined or (("防雨" in combined or "室外" in combined) and ("帽" in combined or "檐" in combined)):
        b_type = "安装板+帽子"
    elif "空箱-横梁" in combined or ("空箱" in combined and "横梁" in combined):
        b_type = "空箱-横梁"
    elif "空箱" in combined:
        b_type = "空箱"
    elif "面盖" in combined or "单独面盖" in combined or "仅门" in combined:
        b_type = "面盖"
    elif "双门" in combined or "带内门" in combined or "二层门" in combined:
        b_type = "双门"
    elif "横梁" in combined:
        b_type = "横梁"
    else:
        # 未注结构时，根据箱体物理规格确定性推导：
        # 落地柜/高度>=1600mm：重型设备必须配备安装板+支撑支架+封板骨架
        # 挂墙小型箱体：标配单层元件安装底板
        if is_floor or max(w, h) >= 1600:
            b_type = "安装板+支架+封板"
        else:
            b_type = "安装板"

    return b_type, material, thickness


def estimate_box_enclosure_price(box: dict, circuits_count: int) -> Tuple[float, str]:
    """估算配电箱外壳制造成本。

    1. 优先使用物理钣金展开面积法（提取 box.size / dimensions 中的 WxHxD 规格，动态解析材质、板厚、立板/支架结构）；
    2. 无具体尺寸标注时，无缝回退至回路数与落地/挂墙形式定额推导，标明显式估算基准，绝不伪造尺寸。
    """
    ip = str(box.get("ip_rating") or "IP30").upper()
    box_type = str(box.get("box_type") or "").upper()
    size_raw = str(box.get("size") or box.get("box_type") or "")
    is_floor = "落地" in str(box.get("install_type") or box.get("install") or "") or "GGD" in box_type or "XL" in box_type

    # 1. 物理钣金展开面积法
    dims = parse_box_dimensions(size_raw)
    if dims:
        w, h, d = dims
        b_type, material, thk = resolve_box_enclosure_specs(box, w, h, d, is_floor)
        unfold = calc_enclosure_unfolding(w, h, d, box_type=b_type, material=material, thickness=thk)
        price = unfold["total_price"]
        desc = unfold["desc"]
        for ip_prefix, prem in IP_PREMIUMS.items():
            if ip_prefix in ip:
                price = round(price * float(prem), 2)
                desc += f" [{ip_prefix}高防护处理]"
                break
        return price, desc

    # 2. 传统定额规则兜底（未注物理尺寸时，严禁假装实测尺寸，如实标明显式估算基准）
    tier_found = None
    for tier in CIRCUIT_TIER_PRICES:
        if circuits_count >= tier.get("min_circuits", 0):
            tier_found = tier
            break
    if not tier_found:
        return 0.0, UNKNOWN_PRICE_BASIS
    base_price = float(tier_found["price"])
    model_desc = tier_found["desc"].format(circuits_count=circuits_count)

    for ip_prefix, prem in IP_PREMIUMS.items():
        if ip_prefix in ip:
            base_price = round(base_price * float(prem), 2)
            model_desc += f" [{ip_prefix}高防护处理]"
            break

    return round(base_price, 2), model_desc


def estimate_copper_busbar_cost(main_current_a: int, box_width_m: float = 0.8) -> Tuple[float, float, str]:
    """根据进线主开关电流计算母排铜排重量与成本。若电流未提供或 <=0，绝不默认 63A，返回待核定。"""
    if main_current_a <= 0:
        return 0.0, 0.0, "主回路额定电流未标注（未计入主铜排费用，待核定）"

    matched_spec = BUSBAR_SPECS[0]
    for spec in BUSBAR_SPECS:
        if main_current_a <= spec[0]:
            matched_spec = spec
            break
    else:
        matched_spec = BUSBAR_SPECS[-1]

    _, spec_code, weight_per_m = matched_spec
    # 三相主母线(3根) + N排(1根) + PE地排(1根)
    allow_main = float(DEFAULTS.get("busbar_allowance_main_m", 0.4))
    allow_pe_n = float(DEFAULTS.get("busbar_allowance_pe_n_m", 0.2))
    total_length_m = (box_width_m + allow_main) * 3 + (box_width_m + allow_pe_n) * 2
    total_weight_kg = round(total_length_m * weight_per_m, 2)
    busbar_cost = round(total_weight_kg * COPPER_PRICE_PER_KG, 2)
    detail = f"主线电流{main_current_a}A 选用[{spec_code}] 重量{total_weight_kg}kg@￥{COPPER_PRICE_PER_KG}/kg"
    return busbar_cost, total_weight_kg, detail


def calc_copper_bar_quote(
    main_current_a: Optional[int] = None,
    spec: Optional[str] = None,
    length_m: Optional[float] = None,
    qty: int = 1,
    density: float = COPPER_DENSITY,
    price_per_kg: float = COPPER_PRICE_PER_KG,
    cabinet_type: str = "GGD"
) -> dict:
    """计算低压动力柜（GGD/GCK/MNS）12米标准主母排铜排负荷选型与含税造价。

    公式：
      截面积 S(mm²) = 宽 × 厚
      体积 V(cm³)   = S × 长度(m)
      重量 D(kg)    = V × 8.9 ÷ 1000
      报价(元)      = D × 铜排单价(元/kg) × 根数
    """
    if length_m is None:
        length_m = float(DEFAULTS.get("busbar_length_m", 12.0))
    w, h = 0.0, 0.0
    spec_label = ""
    load_range = ""

    if spec:
        try:
            parts = spec.lower().replace("×", "x").split("x")
            w, h = float(parts[0]), float(parts[1])
            spec_label = f"TM-{int(w)}x{int(h)}"
        except Exception:
            pass
    elif main_current_a is not None and main_current_a > 0:
        matched = COPPER_STANDARD_SPECS[-1]
        for s in COPPER_STANDARD_SPECS:
            if main_current_a >= s[0]:
                matched = s
                break
        w, h, spec_label, load_range = matched[1], matched[2], f"TM-{matched[3]}", matched[4]
    else:
        return {
            "spec": "",
            "weight_kg": 0.0,
            "total_price": 0.0,
            "desc": "主回路额定电流未标注（动力母排暂未计价，待核定）",
        }

    s_mm2 = w * h
    v_cm3 = s_mm2 * length_m
    weight_kg = round(v_cm3 * density / 1000.0 * qty, 3)
    quote_total = round(weight_kg * price_per_kg, 2)
    desc = f"{cabinet_type}动力柜标准12m铜排 [{spec_label} 载流{load_range or str(main_current_a)+'A'}] 重{weight_kg}kg@￥{price_per_kg}/kg"
    return {
        "spec": spec_label,
        "width_mm": w,
        "thickness_mm": h,
        "length_m": length_m,
        "qty": qty,
        "section_mm2": s_mm2,
        "weight_kg": weight_kg,
        "unit_price_kg": price_per_kg,
        "total_price": quote_total,
        "desc": desc,
    }


def calculate_box_quotation(
    box: dict,
    circuits: list[dict],
    components: list[dict],
    brand: str = "",
    profit_rate: Optional[float] = None,
    tax_rate: Optional[float] = None,
    use_ab_model: bool = False,
    ab_mode: str = "auto"
) -> dict:
    """计算单个成套配电箱柜的完整工业造价明细

    兼具成套厂通行的 A/B 定额六步组价法与传统制造工时定额，
    默认输出满足既有 excel/app 契约，并挂载完整的 ab_quotation 结构。
    """
    # 1. 元器件造价合计 (全面覆盖传入元器件与回路中断路器开关)
    comp_total = 0.0
    comp_list_total = 0.0
    priced_components = []

    # 控制/信号电缆回路（至操作柱/PLC、KVV/KYJVP 等）不是断路器出线：
    # 不生成断路器、不计入断路器回路数与出线组装工时，但仍在回路清单里可见。
    from .assemble import is_control_cable_circuit

    switch_circuits: list[dict] = []
    control_circuits: list[dict] = []
    for c in circuits:
        (control_circuits if is_control_cable_circuit(c) else switch_circuits).append(c)

    # 断路器不在组价里自己合成：没有断路器元器件的柜，由 box_components 拆分时
    # 按回路补出行（标“由回路图提取”），拆量页与组价永远同一份清单。
    for comp in components or []:
        spec = comp.get("spec") or ""
        # 数量缺失按 1 件（外部直接传元器件的旧契约）；显式 0 就是 0，不凭空记一件
        raw_qty = comp.get("quantity")
        try:
            qty = 1 if raw_qty in (None, "") else int(float(raw_qty))
        except (TypeError, ValueError):
            qty = 1
        unit_price, list_price, basis = calculate_component_unit_price(spec, brand=brand, category=comp.get("category", ""))
        item_total = round(unit_price * qty, 2)
        comp_total += item_total
        comp_list_total += list_price * qty
        priced_components.append({
            "name": comp.get("name") or comp.get("standard_name") or "元器件",
            "spec": spec,
            "quantity": qty,
            "unit": comp.get("unit") or "台",
            "unit_price": unit_price,
            "list_price": list_price,
            "total_price": item_total,
            "pricing_basis": basis,
        })

    # 2. 箱体外壳成本（按断路器回路数取档，控制电缆回路不占开关位）
    circuits_count = len(switch_circuits)
    enclosure_cost, enclosure_desc = estimate_box_enclosure_price(box, circuits_count)

    # 3. 铜排母线成本 (按最大进线断路器或总回路估算额定电流)
    main_curr = 0
    for c in switch_circuits:
        if c.get("circuit_type") == "incoming" or "进线" in str(c.get("load_name") or "") or "进线" in str(c.get("circuit_no") or ""):
            feat = parse_component_features(c.get("breaker_spec", "") or c.get("breaker", ""))
            if feat.get("current_a"):
                main_curr = max(main_curr, feat["current_a"])

    # 若回路未明确标注进线，从箱体属性或技术备注中推导主开关规格
    if main_curr == 0:
        for k in ("main_switch", "incoming_spec", "main_breaker", "note"):
            val = str(box.get(k) or "")
            if val:
                feat = parse_component_features(val)
                if feat.get("current_a"):
                    main_curr = max(main_curr, feat["current_a"])
                    break

    box_type = str(box.get("box_type") or "").upper()
    is_power_cab = "GGD" in box_type or "GCK" in box_type or "MNS" in box_type or "动力柜" in box_type
    size_raw = str(box.get("size") or box.get("box_type") or "")
    dims = parse_box_dimensions(size_raw)
    box_w_m = (dims[0] / 1000.0) if dims and dims[0] > 0 else float(DEFAULTS.get("box_width_m", 0.8))

    if main_curr > 0:
        if is_power_cab and main_curr >= 250:
            copper_q = calc_copper_bar_quote(main_curr, cabinet_type=box_type or "GGD")
            busbar_cost = copper_q["total_price"]
            copper_weight_kg = copper_q["weight_kg"]
            busbar_desc = copper_q["desc"]
        else:
            busbar_cost, copper_weight_kg, busbar_desc = estimate_copper_busbar_cost(main_curr, box_width_m=box_w_m)
    else:
        busbar_cost, copper_weight_kg, busbar_desc = 0.0, 0.0, "进线规格待确认（主母排暂未计价）"

    # 4. 二次线及辅材 (传统定额)
    auxiliary_cost = round(comp_total * AUXILIARY_RATE, 2)

    # 5. 人工组装调试费 (传统定额)
    incoming_count = sum(1 for c in switch_circuits if c.get("circuit_type") == "incoming" or "进线" in str(c.get("load_name") or "") or "进线" in str(c.get("circuit_no") or ""))
    outgoing_count = max(0, circuits_count - incoming_count)
    labor_cost = round(incoming_count * LABOR_INCOMING_RATE + outgoing_count * LABOR_OUTGOING_RATE, 2)

    # 6. 型式试验与CCC分摊 (一般按出厂价分摊，费率来自 config/pricing.json)
    factory_cost = enclosure_cost + comp_total + busbar_cost + auxiliary_cost + labor_cost
    test_cert_cost = round(factory_cost * TEST_CERT_RATE, 2)

    # 7. 制造总成本与传统含税出厂报价
    p_rate = profit_rate if profit_rate is not None else PROFIT_RATE_DEFAULT
    t_rate = tax_rate if tax_rate is not None else TAX_RATE_DEFAULT
    total_cost = factory_cost + test_cert_cost
    subtotal_with_profit = total_cost * (1.0 + p_rate)
    trad_tax_included = round(subtotal_with_profit * (1.0 + t_rate), 2)

    # 8. 行业标准 A/B 费率定额模型计算 (造价极客标准六步法)
    is_jv = (norm_brand(brand) in JOINT_VENTURE_BRANDS) if ab_mode == "auto" else (ab_mode == "joint-venture")
    ab_info = AB_RATES["joint-venture"] if is_jv else AB_RATES["domestic"]
    A_rate = ab_info["A"]
    B_rate = ab_info["B"]

    sum_main = round(comp_total, 2)
    sum_aux = round(sum_main * A_rate, 2)
    sum_mat = round(sum_main + sum_aux, 2)
    sum_set = round(sum_mat * B_rate, 2)
    sum_box = round(enclosure_cost + busbar_cost, 2)
    sum_total = round(sum_mat + sum_set + sum_box, 2)

    ab_quotation = {
        "sum_main": sum_main,
        "sum_aux": sum_aux,
        "sum_mat": sum_mat,
        "sum_set": sum_set,
        "sum_box": sum_box,
        "sum_total": sum_total,
        "A": A_rate,
        "B": B_rate,
        "ab_label": ab_info["label"],
        "steps": [
            {"step": "①", "name": "主要元件", "amount": sum_main, "formula": "元器件含税采购合计"},
            {"step": "②", "name": "辅材", "amount": sum_aux, "formula": f"① × {A_rate*100:.1f}%"},
            {"step": "③", "name": "材料合计", "amount": sum_mat, "formula": "① + ②"},
            {"step": "④", "name": "成套费用", "amount": sum_set, "formula": f"③ × {B_rate*100:.1f}%"},
            {"step": "⑤", "name": "箱体及母排", "amount": sum_box, "formula": f"外壳 ￥{enclosure_cost:.2f} + 母排 ￥{busbar_cost:.2f}" if busbar_cost > 0 else enclosure_desc},
            {"step": "⑥", "name": "单箱总报价", "amount": sum_total, "formula": "③ + ④ + ⑤"},
        ]
    }

    final_price = sum_total if use_ab_model else trad_tax_included

    return {
        "box_code": box.get("box_code") or "未命名箱体",
        "box_name": box.get("box_name") or box.get("name") or "",
        "brand": brand,
        "circuits_count": circuits_count,
        "control_circuits_count": len(control_circuits),
        "cost_breakdown": {
            "enclosure_cost": enclosure_cost,
            "enclosure_desc": enclosure_desc,
            "component_cost": round(comp_total, 2),
            "component_list_total": round(comp_list_total, 2),
            "copper_busbar_cost": busbar_cost,
            "copper_weight_kg": copper_weight_kg,
            "copper_desc": busbar_desc,
            "auxiliary_cost": auxiliary_cost,
            "labor_cost": labor_cost,
            "test_cert_cost": test_cert_cost,
            "factory_total_cost": round(total_cost, 2),
        },
        "ab_quotation": ab_quotation,
        "profit_rate": p_rate,
        "tax_rate": t_rate,
        "final_tax_included": final_price,
        "components": priced_components,
    }


def calculate_box_ab_quotation(
    box: dict,
    circuits: list[dict],
    components: list[dict],
    brand: str = "",
    ab_mode: str = "auto"
) -> dict:
    """便捷调用：直接采用工业 A/B 差异化定额模型计算单箱出厂报价"""
    return calculate_box_quotation(box, circuits, components, brand=brand, use_ab_model=True, ab_mode=ab_mode)


def compare_brands_quotation(
    box: dict,
    circuits: list[dict],
    components: list[dict]
) -> dict:
    """横向多品牌比价矩阵 (外资一线 施耐德/ABB vs 国产良信/正泰/德力西)"""
    results = {}
    for brand in COMPARISON_BRANDS:
        q = calculate_box_quotation(box, circuits, components, brand=brand)
        results[brand] = {
            "final_amount": q["final_tax_included"],
            "component_cost": q["cost_breakdown"]["component_cost"],
            "factory_cost": q["cost_breakdown"]["factory_total_cost"],
        }

    schneider_amount = results[BENCHMARK_BRAND]["final_amount"]
    chint_amount = results[DOMESTIC_SUBSTITUTE]["final_amount"]
    saving_ratio = round((schneider_amount - chint_amount) / schneider_amount * 100, 1) if schneider_amount else 0

    return {
        "brands": results,
        "benchmark_brand": BENCHMARK_BRAND,
        "domestic_substitute": DOMESTIC_SUBSTITUTE,
        "total_saving_ratio_pct": saving_ratio,
    }


def _tier(tiers: list, value: float) -> dict:
    """按 max 升序取第一个容得下 value 的档位；max 为 null 的是最后一档。"""
    for t in tiers:
        if t.get("max") is None or value <= float(t["max"]):
            return t
    return tiers[-1]


def estimate_reference_price(comp_name: str, raw_spec: str, category: str = "") -> dict:
    """价格库没收录时给报价员看的经验定额参考价（config/pricing.json 的 reference_estimates）。

    数字是内置经验值，不联网、不是供应商报价；source 如实写“定额估算”，前端需人工确认后才采用。
    """
    c_name = (comp_name or "").strip()
    spec = (raw_spec or "").strip()
    full_str = f"{c_name} {spec}".upper()

    def out(model: str, price: float, brand: str) -> dict:
        return {"model": model, "price": round(float(price), 2), "brand": brand,
                "source": REFERENCE_ESTIMATE_SOURCE, "reference": REFERENCE_ESTIMATE_BASIS}

    def current(default: int) -> int:
        m = re.search(r"(\d+)\s*A", spec, re.IGNORECASE)
        return int(m.group(1)) if m else default

    # 1. 配电箱体 / 柜体：按尺寸与安装形式
    if any(k in c_name for k in ("箱体", "配电箱", "配电柜", "动力箱", "基业箱")):
        cfg = _REF_EST["enclosure"]
        dims = parse_box_dimensions(spec)
        if not dims:
            return out(cfg["no_dims"]["model"].format(spec=spec or "定制规格"), cfg["no_dims"]["price"], cfg["brand"])
        w, h, d = (int(x) for x in dims)
        if h >= cfg["floor_min_height_mm"] or "柜" in c_name or any(k in full_str for k in cfg["floor_keywords"]):
            row = cfg["floor"]
        elif h >= cfg["power_min_height_mm"]:
            row = cfg["power"]
        else:
            row = cfg["small"]
        return out(row["model"].format(w=w, h=h, d=d), row["price"], cfg["brand"])

    # 2. 电流互感器 (CT)：按变比一次电流分档
    if "互感器" in c_name or "CT" in full_str or re.search(r"\b\d+/5\b", spec):
        cfg = _REF_EST["ct"]
        m = re.search(r"(\d+)/5", spec)
        curr = int(m.group(1)) if m else int(cfg["default_ratio"])
        return out(cfg["model"].format(curr=curr), _tier(cfg["tiers"], curr)["price"], cfg["brand"])

    # 3. 浪涌保护器 / 防雷器 (SPD)
    if "浪涌" in c_name or "防雷" in c_name or "SPD" in full_str:
        cfg = _REF_EST["spd"]
        row = cfg["t1"] if any(k in full_str for k in cfg["t1_keywords"]) else cfg["t2"]
        return out(row["model"], row["price"], row["brand"])

    # 4. 多功能电表 / 电度表
    if "电表" in c_name or "仪表" in c_name or "WH" in full_str or "多功能" in c_name:
        cfg = _REF_EST["meter"]
        return out(cfg["model"], cfg["price"], cfg["brand"])

    # 5. 交流接触器
    if "接触器" in c_name:
        cfg = _REF_EST["contactor"]
        curr = current(int(cfg["default_current"]))
        t = _tier(cfg["tiers"], curr)
        return out(cfg["model"].format(series=t["series"], curr=curr), t["price"], cfg["brand"])

    # 6. 热继电器：按整定范围上限分档
    if "热继电器" in c_name or "热过载" in c_name or "~" in spec:
        cfg = _REF_EST["thermal_relay"]
        m = re.search(r"[\d.]+\s*~\s*([\d.]+)", spec)
        top = float(m.group(1)) if m else float(cfg["default_current"])
        t = _tier(cfg["tiers"], top)
        return out(cfg["model"].format(series=t["series"], spec=spec), t["price"], cfg["brand"])

    # 7. 隔离开关 / 刀熔开关
    if "隔离开关" in c_name or "刀熔" in c_name or "HR6" in full_str:
        cfg = _REF_EST["isolator"]
        curr = current(int(cfg["default_current"]))
        return out(cfg["model"].format(curr=curr), _tier(cfg["tiers"], curr)["price"], cfg["brand"])

    # 8. 漏电断路器 (RCBO)
    if "漏电" in c_name or "RCBO" in full_str or "30MA" in full_str:
        cfg = _REF_EST["rcbo"]
        feats = parse_component_features(spec, category)
        curr = feats.get("current_a") or cfg["default_current"]
        poles = feats.get("poles") or cfg["default_poles"]
        row = cfg["multi_pole"] if ("3P" in poles or "4P" in poles) else cfg["single_pole"]
        return out(row["model"].format(curr=curr), row["price"], cfg["brand"])

    # 9. 微型断路器 (MCB)
    if "微断" in c_name or "微型断路器" in c_name or "MCB" in full_str:
        cfg = _REF_EST["mcb"]
        feats = parse_component_features(spec, category)
        curr = feats.get("current_a") or cfg["default_current"]
        poles = feats.get("poles") or cfg["default_poles"]
        price = cfg["pole_prices"].get(poles, cfg["other_price"])
        return out(cfg["model"].format(poles=poles, curr=curr), price, cfg["brand"])

    # 10. 塑壳断路器 (MCCB)
    if "塑壳" in c_name or "MCCB" in full_str:
        cfg = _REF_EST["mccb"]
        feats = parse_component_features(spec, category)
        curr = feats.get("current_a") or cfg["default_current"]
        poles = feats.get("poles") or cfg["default_poles"]
        model = cfg["model"].format(frame=max(curr, cfg["min_frame"]), poles=poles, curr=curr)
        return out(model, _tier(cfg["tiers"], curr)["price"], cfg["brand"])

    # 兜底：按额定电流线性估
    cfg = _REF_EST["fallback"]
    curr = parse_component_features(spec, category).get("current_a")
    price = cfg["price_no_current"] if not curr else min(max(float(curr) * cfg["per_amp"], cfg["min"]), cfg["max"])
    return out(f"{c_name} {spec}".strip(), price, cfg["brand"])


def get_component_price_candidates(raw_spec: str, category: str = "", comp_name: str = "") -> dict:
    """查询元器件在各个品牌的候选匹配型号与采购单价，含价库命中与定额估算参考价（ai_suggested 键沿用前端字段名）"""
    feats = parse_component_features(raw_spec, category)
    cat = feats.get("category") or ""
    poles = feats.get("poles") or ""
    frame = feats.get("frame_a")
    curr = feats.get("current_a")
    results = {}
    for brand in CANDIDATE_LOOKUP_BRANDS:
        match = lookup_price_library(
            cat,
            brand,
            poles=poles,
            curr_a=curr,
            raw_spec=raw_spec,
            curve=feats.get("curve", "C"),
            breaking_ka=feats.get("breaking_ka", 6),
            rel_code=feats.get("rel_code", ""),
            frame_a=frame,
        )
        if match:
            results[brand] = match

    def first(order: tuple, source: str) -> Optional[dict]:
        brand = next((b for b in order if b in results), None)
        if brand is None:
            return None
        m = results[brand]
        # 两张候选卡片都来自同一个内置价格库，只是品牌优先顺序不同，来源必须写真实出处
        return {"brand": brand, "model": m["model"], "price": m["price_tax"], "source": f"{source}·{brand}"}

    info_cand = first(INFO_PRICE_ORDER, "价库")
    market_cand = first(MARKET_PRICE_ORDER, "价库")
    ai_sugg = estimate_reference_price(comp_name, raw_spec, category)

    return {
        "library": results,
        "info": info_cand,
        "market": market_cand,
        "ai_suggested": ai_sugg,
    }
