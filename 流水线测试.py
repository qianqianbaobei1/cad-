#!/usr/bin/env python3
"""直接测试 AI + 知识库 完整拆解流程"""
from __future__ import annotations

import os, sys, json, re, time, argparse, hashlib, csv, asyncio
from datetime import datetime
from dataclasses import dataclass, field

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_DATA_DIR = os.path.join(SCRIPT_DIR, "标准知识库", "源数据")
CSV_CACHE: dict[str, list[dict]] = {}
MATERIAL_NAME_KB_PATH = os.path.join(SCRIPT_DIR, "项目数据", "材料名库.json")
MATERIAL_NAME_KB_CACHE = None
PROJECT_DEFAULTS_KB_PATH = os.path.join(SCRIPT_DIR, "项目数据", "项目默认值库.json")
PROJECT_DEFAULTS_KB_CACHE = None

# ── CSV 文件名 → 源数据子路径 ──
_SOURCE_PATH_MAP = {
    "Q0_清单项目编码_房建工程.csv": "01_定额库/Q0_清单项目编码_房建工程.csv",
    "Q0_清单项目编码_安装工程.csv": "01_定额库/Q0_清单项目编码_安装工程.csv",
    "T3_房建_标准物料库.csv": "01_房屋建筑与装饰工程/CSV导出/03_t3_标准物料库.csv",
    "T3_安装_标准物料库.csv": "02_通用安装工程/CSV导出/02_t3_标准物料库.csv",
    "品类节点索引.csv": "06_品类树/品类节点索引.csv",
    "T3_品类映射.csv": "06_品类树/T3_品类映射.csv",
    "N1_国家规范索引.csv": "03_国家规范库/N1_国家规范索引.csv",
    "N5_材料规范映射.csv": "03_国家规范库/N5_材料规范映射.csv",
    "Q1_定额索引.csv": "01_定额库/Q1_定额索引.csv",
    "Q2_定额材料消耗.csv": "01_定额库/Q2_定额材料消耗.csv",
    "N3_材料技术参数定义.csv": "03_国家规范库/N3_材料技术参数定义.csv",
    "N6_规范校验规则.csv": "03_国家规范库/N6_规范校验规则.csv",
}

# 加载 .env
env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
with open(env_file) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()


# ══════════════════════════════════════
# PromptPlan — Stage0 输出的专用提示词计划
# ══════════════════════════════════════

@dataclass
class PromptPlan:
    """Stage0 输出的专用提示词计划。AI 生成核心内容，代码注入安全外壳。"""
    stage1_prompt: str = ""
    stage2_prompt: str = ""
    stage5_prompt: str = ""
    quantity_strategy: dict = field(default_factory=dict)
    forbidden_materials: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    knowledge_conflicts: list[dict] = field(default_factory=list)
    must_include_materials: list[str] = field(default_factory=list)

    # 安全外壳 — 代码固定注入，不被 AI 输出覆盖
    SAFETY_SHELL = (
        "\n\n【硬性约束 — 由系统强制注入，不可违反】\n"
        "1. 输出格式必须严格遵循上述 JSON schema，不得输出 JSON 之外的任何文字。\n"
        "2. 优先使用 T3 标准物料库中的标准名称；若 T3 库中无完全匹配项，保留输入/特征中的原始材料名称，"
        "标记 confidence=low，严禁替换为 T3 库中名称近似但材质/类型不同的材料。"
        "例如：输入明示「钢制保温门」，T3 库中无此项时保留「钢制保温门」，"
        "不得替换为「冷藏库门」「铝合金门」「塑钢门」等近似但本质不同的 T3 条目。\n"
        "3. 禁止编造材料名称、规格、单位、损耗率。不确定的参数留空或标记为 pending。\n"
        "4. 禁止输出人工、机械、运输服务等非采购对象。\n"
        "5. 如果知识库信息与输入特征冲突，以输入特征为准，并在 evidence 中说明冲突原因。\n"
        "6. 项目默认推荐库（project_defaults_kb）仅作参考锚点，若其推荐的材质/类型与输入特征明示的不一致，"
        "不得采纳默认推荐，必须以输入特征为准。\n"
    )

    def wrap_stage_prompt(self, stage: str, ai_generated: str) -> str:
        """将 AI 生成的提示词用安全外壳包裹"""
        if not ai_generated:
            return ""
        return ai_generated + self.SAFETY_SHELL


# ══════════════════════════════════════
# T3 动态材料词库
# ══════════════════════════════════════

_t3_lexicon: list[tuple[str, str]] | None = None  # [(keyword, source), ...]


def build_material_lexicon_from_t3() -> list[tuple[str, str]]:
    """从 T3 标准物料库动态构建材料关键词词库。
    返回 [(关键词, 来源), ...]，来源为 '标准名称' / '别名' / '特征关键词'。
    """
    global _t3_lexicon
    if _t3_lexicon is not None:
        return _t3_lexicon

    lexicon: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in t3_catalog_rows():
        # 材料抽取只使用“标准名称/别名”。特征关键词里常有 C30、B1、厚度等规格词，
        # 不能直接作为材料名抽出；这些词仍在 T3 匹配评分阶段使用。
        for field, source in [("标准名称", "标准名称"), ("别名", "别名")]:
            items = parse_json_list_field(row.get(field, "")) if field != "标准名称" else [row.get("标准名称", "")]
            for item in items:
                item = compact_text(item)
                if not item or len(item) < 2:
                    continue
                if item in seen:
                    continue
                seen.add(item)
                lexicon.append((item, source))
    # 按关键词长度降序排列，长词优先匹配
    lexicon.sort(key=lambda x: -len(x[0]))
    _t3_lexicon = lexicon
    return lexicon


def refresh_t3_lexicon():
    """热更新 T3 词库（人工确认 KB 写入后调用）"""
    global _t3_lexicon
    _t3_lexicon = None
    return build_material_lexicon_from_t3()


# 不能作为材料名提取的泛化/占位关键词
_LEXICON_STOP_WORDS = {
    "其他", "零星", "杂项", "辅材", "按项", "主材", "材料", "配件",
    "综合", "其它", "等", "项", "套", "批", "组",
}
# 纯规格代号模式（C\d+、M\d+等），不应作为材料名提取
_SPEC_CODE_PATTERN = re.compile(r"^[A-Z]\d+(-\d+)?$")


def _lexicon_keyword_usable(keyword: str) -> bool:
    """过滤不能作为独立材料名提取的关键词。"""
    kw = keyword.strip()
    if not kw or len(kw) < 2:
        return False
    if kw in _LEXICON_STOP_WORDS:
        return False
    if _SPEC_CODE_PATTERN.match(kw):
        return False
    return True


def extract_materials_by_lexicon(text: str, lexicon: list[tuple[str, str]] | None = None) -> list[str]:
    """用词库从文本中提取材料名。不再使用硬编码关键词列表。"""
    if lexicon is None:
        lexicon = build_material_lexicon_from_t3()
    compact = compact_text(text).replace("：", ":")
    result: list[str] = []
    for keyword, source in lexicon:
        if not _lexicon_keyword_usable(keyword):
            continue
        kw_compact = compact_text(keyword).replace("：", ":")
        if kw_compact in compact and keyword not in result:
            result.append(keyword)
    return result


# ----------- 参数与推理辅助 -----------
def parse_args():
    parser = argparse.ArgumentParser(description="AI + 知识库工程量清单材料拆解测试")
    parser.add_argument("--code", default="010502001", help="清单编码，例如 010502001")
    parser.add_argument("--name", default="矩形柱（现浇混凝土）", help="清单名称，例如 矩形柱（现浇混凝土）")
    parser.add_argument("--feature", default="C30", help="项目特征，例如 C30；厚度20mm；1:3水泥砂浆")
    parser.add_argument("--qty", type=float, default=48.6, help="工程量，例如 48.6")
    parser.add_argument("--unit", default="m³", help="清单单位，例如 m³、m²、m、t、项")
    parser.add_argument("--t1", default="", help="T1分部，可为空，例如 混凝土及钢筋混凝土工程")
    parser.add_argument("--component", default="", help="构件/部位，可为空，例如 柱、梁、墙、楼地面")
    parser.add_argument("--input", default="", help="清单输入文件路径，支持 .txt/.json/.csv/.xlsx；为空时使用命令行单条清单参数")
    parser.add_argument("--kb-dir", default="", help="知识库目录路径；为空时优先读取 ./kb，仍为空则使用内置样例知识库")
    parser.add_argument("--output", default="", help="结果输出 JSON 文件路径，可为空")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少条清单；0表示不限制")
    parser.add_argument("--parse-only", action="store_true", help="只解析输入清单并打印预览，不调用AI、不执行拆解")
    parser.add_argument("--interactive", action="store_true", help="交互式输入清单文本，输入 END 后开始拆解")
    parser.add_argument("--sample", action="store_true", help="使用内置样例清单，不进入交互输入")
    parser.add_argument("--learn", action="store_true", help="开启人工确认与知识写回流程")
    parser.add_argument("--approved-kb", default="", help="人工确认知识库文件路径，默认使用 kb/approved_material_rules.json")
    parser.add_argument("--provider", "-p", choices=["deepseek", "ark", "moonshot", "auto"], default="auto",
                        help="选择大模型厂商：deepseek / ark(豆包) / moonshot / auto(自动检测)")
    # ── 项目级参数（用于项目默认值库精准匹配）─────────────────────
    parser.add_argument("--project-category", default="", help="项目类别，如 居住建筑/办公建筑/商业建筑/工业建筑")
    parser.add_argument("--structure-type", default="", help="结构类型，如 框架结构/剪力墙结构/钢结构/框架剪力墙结构")
    parser.add_argument("--height-scope", default="", help="高度范围，如 多层/高层/超高层")
    parser.add_argument("--project-region", default="", help="项目地区，如 华北/华东/华南")
    # ── 对比模式 ─────────────────────────────────────────────────
    parser.add_argument("--compare", action="store_true", help="对比模式：分别以有/无项目信息各跑一次，输出差异摘要")
    return parser.parse_args()

def _source_path(filename: str) -> str:
    """将 CSV 文件名映射到 标准知识库/源数据/ 下的完整路径。"""
    subpath = _SOURCE_PATH_MAP.get(filename, filename)
    return os.path.join(SOURCE_DATA_DIR, subpath)


def load_csv_rows(filename: str) -> list[dict]:
    """读取 标准知识库/源数据/ 中的 CSV 文件。文件不存在时返回空列表。"""
    path = _source_path(filename)
    if path not in CSV_CACHE:
        if not os.path.exists(path):
            CSV_CACHE[path] = []
        else:
            with open(path, encoding="utf-8-sig", newline="") as f:
                CSV_CACHE[path] = list(csv.DictReader(f))
    return CSV_CACHE[path]


def standard_q0_rows() -> list[dict]:
    return (
        load_csv_rows("Q0_清单项目编码_房建工程.csv")
        + load_csv_rows("Q0_清单项目编码_安装工程.csv")
    )


def t3_catalog_rows() -> list[dict]:
    return (
        load_csv_rows("T3_房建_标准物料库.csv")
        + load_csv_rows("T3_安装_标准物料库.csv")
    )


# ── 品类树（铁建云采三级分类）与 T3→品类映射 ──
CATEGORY_TREE_CACHE = None
T3_CATEGORY_MAP_CACHE = None


def category_tree_rows() -> list[dict]:
    global CATEGORY_TREE_CACHE
    if CATEGORY_TREE_CACHE is not None:
        return CATEGORY_TREE_CACHE
    CATEGORY_TREE_CACHE = load_csv_rows("品类节点索引.csv")
    return CATEGORY_TREE_CACHE


def t3_category_mapping_rows() -> list[dict]:
    global T3_CATEGORY_MAP_CACHE
    if T3_CATEGORY_MAP_CACHE is not None:
        return T3_CATEGORY_MAP_CACHE
    T3_CATEGORY_MAP_CACHE = load_csv_rows("T3_品类映射.csv")
    return T3_CATEGORY_MAP_CACHE


def get_category_tree_by_leaf_id(leaf_id: str) -> dict | None:
    """通过品类树叶子节点ID获取完整三级分类信息。"""
    leaf_id = str(leaf_id).strip()
    for row in category_tree_rows():
        if str(row.get("leaf_id", "")).strip() == leaf_id:
            return row
    return None


def get_category_display_path(material_id: str) -> str:
    """通过物料ID查找品类展示路径。"""
    for row in t3_category_mapping_rows():
        if row.get("material_id", "").strip() == material_id:
            return row.get("display_path", "")
    return ""


# ── 从 T3 标准物料库动态构建分类知识库（替代硬编码 MATERIAL_CLASSIFICATION_KB） ──
_T3_CLASSIFICATION_KB_CACHE = None


def build_classification_kb_from_t3() -> list[dict]:
    """从 T3 标准物料库（房建+安装，共330种）构建完整分类知识库。
    每条规则包含：material_name, aliases, role, category_l1/l2/l3,
    category_path, purchase_unit, material_id, display_path, leaf_id, loss_rate.
    """
    global _T3_CLASSIFICATION_KB_CACHE
    if _T3_CLASSIFICATION_KB_CACHE is not None:
        return _T3_CLASSIFICATION_KB_CACHE

    rules = []
    seen_names = set()

    # 构建 T3→品类映射的快速查找
    t3_to_leaf: dict[str, str] = {}
    for row in t3_category_mapping_rows():
        mid = (row.get("material_id") or "").strip()
        lid = (row.get("leaf_id") or "").strip()
        if mid and lid:
            t3_to_leaf[mid] = lid

    for row in t3_catalog_rows():
        material_name = (row.get("标准名称") or "").strip()
        if not material_name or material_name in seen_names:
            continue
        seen_names.add(material_name)

        aliases_raw = parse_json_list_field(row.get("别名", ""))
        material_id = (row.get("物料ID") or "").strip()

        # 查找品类树信息
        leaf_id = (row.get("品类树节点ID") or "").strip()
        if not leaf_id:
            leaf_id = t3_to_leaf.get(material_id, "")
        ct_row = get_category_tree_by_leaf_id(leaf_id) if leaf_id else None

        display_path = (row.get("品类展示路径") or "").strip()
        if not display_path and ct_row:
            display_path = ct_row.get("display_path", "")

        # 品类树三级分类（采购视角）
        ct_l1 = ct_row.get("level1_name", "") if ct_row else ""
        ct_l2 = ct_row.get("level2_name", "") if ct_row else ""
        ct_l3 = ct_row.get("level3_name", "") if ct_row else ""

        # 损耗率：优先品类树，其次T3自身
        loss_rate = None
        if ct_row:
            try:
                loss_rate = float(ct_row.get("loss_rate", ""))
            except (ValueError, TypeError):
                pass
        if loss_rate is None:
            try:
                loss_rate = float(row.get("品类标准损耗率", ""))
            except (ValueError, TypeError):
                loss_rate = None

        rules.append({
            "material_name": material_name,
            "aliases": aliases_raw,
            "role": "主材",
            "category_l1": (row.get("分类(一级)") or "").strip(),
            "category_l2": (row.get("分类(二级)") or "").strip(),
            "category_l3": (row.get("分类(三级)") or "").strip(),
            "category_path": (row.get("分类路径") or "").strip(),
            "purchase_unit": (row.get("采购单位") or "m").strip(),
            "material_id": material_id,
            "leaf_id": leaf_id,
            "display_path": display_path,
            "ct_l1": ct_l1,
            "ct_l2": ct_l2,
            "ct_l3": ct_l3,
            "loss_rate": loss_rate,
            "standard_code": (row.get("标准代号") or "").strip(),
        })

    _T3_CLASSIFICATION_KB_CACHE = rules
    return rules


# ── 从 T3 + 品类树动态构建损耗率知识库（替代硬编码 LOSS_RATE_KB） ──
_T3_LOSS_KB_CACHE = None


def build_loss_kb_from_t3() -> list[dict]:
    """从 T3 标准物料库 + 品类树构建损耗率知识库。
    每条规则包含：material_name, aliases, loss_rate, loss_basis."""
    global _T3_LOSS_KB_CACHE
    if _T3_LOSS_KB_CACHE is not None:
        return _T3_LOSS_KB_CACHE

    rules = []
    for row in build_classification_kb_from_t3():
        if row.get("loss_rate") is None:
            continue
        rules.append({
            "material_name": row["material_name"],
            "aliases": row["aliases"],
            "loss_rate": row["loss_rate"],
            "loss_basis": f"品类树标准损耗率，物料 {row['material_id']}",
        })
    _T3_LOSS_KB_CACHE = rules
    return rules


def n1_standard_rows() -> list[dict]:
    return load_csv_rows("N1_国家规范索引.csv")


def n5_material_standard_rows() -> list[dict]:
    return load_csv_rows("N5_材料规范映射.csv")


def n3_param_rows() -> list[dict]:
    return load_csv_rows("N3_材料技术参数定义.csv")


def n6_rule_rows() -> list[dict]:
    return load_csv_rows("N6_规范校验规则.csv")


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def material_identity_key(name: str) -> str:
    """用于去重的材料身份键，处理常见同义写法。"""
    text = compact_text(name)
    text = text.replace("断热桥", "断桥")
    text = text.replace("（", "(").replace("）", ")")
    text = text.rstrip("()（），,、")
    text = re.sub(r"聚氨酯发泡剂\d+(?:\.\d+)?ml", "聚氨酯发泡剂", text, flags=re.I)
    text = text.replace("管道支架(型钢制", "管道支架(型钢制)")
    return text


def normalize_unit(unit: str) -> str:
    unit = compact_text(unit).lower()
    unit = unit.replace("㎡", "m²").replace("m2", "m²").replace("平方米", "m²")
    unit = unit.replace("m3", "m³").replace("立方米", "m³")
    unit = unit.replace("吨", "t").replace("lt", "t")
    return unit


def units_compatible(a: str, b: str) -> bool:
    a, b = normalize_unit(a), normalize_unit(b)
    if not a or not b:
        return True
    return a == b


def normalize_code(code: str) -> str:
    return re.sub(r"\D", "", str(code or ""))


def code_fallback_candidates(code: str) -> list[dict]:
    """按国标清单编码层级从长到短回退：顺序码 -> 项目码 -> 分部 -> 章节 -> 专业。"""
    digits = normalize_code(code)
    if not digits:
        return []
    level_names = {
        12: "清单顺序码",
        9: "国标项目编码",
        6: "分部编码",
        4: "章节/分部前缀",
        2: "专业工程代码",
    }
    lengths = [len(digits)]
    for n in [9, 6, 4, 2]:
        if n < len(digits):
            lengths.append(n)
    seen, candidates = set(), []
    for n in lengths:
        cand = digits[:n]
        if cand in seen:
            continue
        seen.add(cand)
        candidates.append({"code": cand, "length": n, "level": level_names.get(n, f"{n}位编码")})
    return candidates


def split_numbered_items(text: str) -> list[str]:
    text = str(text or "").replace("\r\n", "\n")
    text = re.sub(r"(?<!\n)(\d+\s*[.、)])", r"\n\1", text)
    items = []
    for line in text.splitlines():
        line = re.sub(r"^\s*\d+\s*[.、)]\s*", "", line).strip()
        line = re.sub(r"\s+", "", line)
        if line:
            items.append(line)
    return items


def parse_json_list_field(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    if text in {"", "NULL", "null", "[]"}:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    except Exception:
        pass
    return [x.strip().strip('"').strip("'") for x in re.split(r"[,，、;；]", text.strip("[]")) if x.strip()]


def parse_spec_pattern(value) -> list[dict]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def load_material_name_kb() -> dict:
    """读取正规采购物料名称知识库。缺失时返回空结构，不阻断主流程。"""
    global MATERIAL_NAME_KB_CACHE
    if MATERIAL_NAME_KB_CACHE is not None:
        return MATERIAL_NAME_KB_CACHE
    if not os.path.exists(MATERIAL_NAME_KB_PATH):
        MATERIAL_NAME_KB_CACHE = {}
        return MATERIAL_NAME_KB_CACHE
    try:
        with open(MATERIAL_NAME_KB_PATH, encoding="utf-8") as f:
            MATERIAL_NAME_KB_CACHE = json.load(f)
    except Exception:
        MATERIAL_NAME_KB_CACHE = {}
    return MATERIAL_NAME_KB_CACHE


def material_name_endings() -> list[str]:
    kb = load_material_name_kb()
    endings = []
    for row in kb.get("material_endings", []) or []:
        ending = row.get("ending", "") if isinstance(row, dict) else str(row)
        ending = compact_text(ending)
        if ending:
            endings.append(ending)
    # 工程清单中高频但云采结尾库未充分覆盖的材料类型，作为辅助结尾词补充。
    endings.extend([
        "灰土", "素土", "密封膏", "嵌缝膏", "油膏", "网格布", "玻纤网格布",
        "挤塑板", "保温板", "粘接剂", "粘结剂", "胶粘剂", "垫块", "脱模剂",
        "界面剂", "石膏", "砂子", "石灰", "薄膜",
    ])
    return sorted(set(endings), key=len, reverse=True)


def material_spec_rules() -> list[dict]:
    rules = load_material_name_kb().get("spec_extraction_rules", []) or []
    if not rules:
        rules = [
            {"pattern": r"C\d{2,3}", "field": "strength_grade", "desc": "混凝土强度等级"},
            {"pattern": r"HRB\d+E?|HPB\d+", "field": "steel_grade", "desc": "钢筋等级"},
            {"pattern": r"DN\d+(?:\.\d+)?", "field": "dn_size", "desc": "公称直径"},
            {"pattern": r"[Φφ]\d+(?:\.\d+)?", "field": "diameter", "desc": "直径"},
            {"pattern": r"B[12]级", "field": "fire_rating", "desc": "防火等级"},
            {"pattern": r"\d+:\d+(?:\.\d+)?", "field": "mix_ratio", "desc": "配合比"},
            {"pattern": r"\d+(?:\.\d+)?\s*mm\s*厚", "field": "thickness", "desc": "厚度"},
            {"pattern": r"\d+(?:\.\d+)?\s*(?:\+\s*\d+(?:\.\d+)?A\s*)+\+\s*\d+(?:\.\d+)?", "field": "glass_composition", "desc": "中空玻璃组成"},
            {"pattern": r"\d+(?:\.\d+)?\s*[×xX]\s*\d+(?:\.\d+)?(?:\s*[×xX]\s*\d+(?:\.\d+)?)?\s*mm", "field": "dimensions", "desc": "尺寸"},
        ]
    return [r for r in rules if isinstance(r, dict) and r.get("pattern")]


def split_spec_from_name(name: str, existing_spec: str = "") -> tuple[str, str, list[dict]]:
    """把混入材料名的规格拆到 spec_hint。返回 core_name, spec_hint, specs。"""
    original = str(name or "").strip()
    work = original
    specs = []
    seen = set()
    for rule in material_spec_rules():
        pattern = rule.get("pattern", "")
        try:
            matches = list(re.finditer(pattern, work, re.I))
        except re.error:
            continue
        for m in matches:
            value = m.group(0).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            specs.append({
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
    spec_values = []
    if existing_spec:
        spec_values.extend([x.strip() for x in re.split(r"[;；,，]", str(existing_spec)) if x.strip()])
    spec_values.extend([s["value"] for s in specs])
    dedup_specs = list(dict.fromkeys(spec_values))
    return core or original, "；".join(dedup_specs), specs


def extract_glass_spec_from_feature(text: str) -> str:
    """提取项目特征中明确写出的中空玻璃组成，优先级高于默认库/T3规格。"""
    normalized = str(text or "").replace("（", "(").replace("）", ")").replace(" ", "")
    match = re.search(r"\d+(?:\.\d+)?(?:\+\d+(?:\.\d+)?A)+\+\d+(?:\.\d+)?", normalized, re.I)
    return match.group(0) if match else ""


def material_has_ending_support(name: str) -> bool:
    compact = compact_text(name)
    return bool(compact and any(compact.endswith(e) for e in material_name_endings()))


def material_has_t3_support(name: str, boq_item: dict) -> bool:
    row, _score = match_t3_material(name, boq_item)
    return bool(row)


def material_has_kb_support(name: str, kb_context: dict) -> bool:
    return bool(
        find_material_rule(name, kb_context.get("material_rules", []))
        or match_kb_record(name, kb_context.get("classification_rules", []))
        or match_kb_record(name, kb_context.get("loss_rules", []))
    )


def validate_material_name(item: dict, kb_context: dict) -> tuple[dict, list[str], bool]:
    """材料名确定性校验。返回 item, issues, invalid。invalid 不进入正式采购材料。"""
    issues = []
    name = str(item.get("material_name", "")).strip()
    compact = compact_text(name)
    if not compact:
        return item, ["空材料名称，无法校验。"], True

    kb = load_material_name_kb()
    boq_item = kb_context.get("boq_item", {})
    hard_invalid = False
    invalid_reasons = []

    # 动作词/占位词是硬拦截；其余广义模式结合 T3/KB 支撑判断，避免误伤“养护剂”等真实材料。
    hard_patterns = [
        (r"^(新增|拆除|安装|绑扎|浇筑|砌筑|抹灰|喷涂|铺设|粘贴)", "动作词前缀，不是材料名"),
        (r"^(其他|待定|详见|参照)$", "占位词或引用词，不是材料名"),
        (r"(其他材料|材料名称|物料名称|详见设计|待定)$", "通用占位词"),
        (r"(人工费|机械费|运输费|服务费|管理费)", "费用项，不是采购材料"),
    ]
    for pattern, reason in hard_patterns:
        if re.search(pattern, name):
            hard_invalid = True
            invalid_reasons.append(reason)

    if len(compact) <= 1:
        hard_invalid = True
        invalid_reasons.append("单字名称过于简短，无法识别")

    t3_supported = material_has_t3_support(name, boq_item)
    kb_supported = material_has_kb_support(name, kb_context)
    ending_supported = material_has_ending_support(name)

    for row in kb.get("illegal_name_patterns", []) or []:
        pattern = row.get("pattern", "") if isinstance(row, dict) else ""
        reason = row.get("reason", "非法材料名模式") if isinstance(row, dict) else "非法材料名模式"
        if not pattern:
            continue
        try:
            matched = re.search(pattern, name)
        except re.error:
            matched = None
        if matched and not (t3_supported or kb_supported):
            # 避免“混凝土养护剂”这类真实物料被宽泛工艺词误杀。
            if reason.startswith("施工工艺") and (name.endswith("剂") or name.endswith("膜") or name.endswith("材料")):
                continue
            hard_invalid = True
            invalid_reasons.append(reason)

    if len(compact) > 40 and not (t3_supported or kb_supported):
        item["needs_name_review"] = True
        issues.append(f"{name} 长度超过正规物料名P99范围，需复核名称是否混入描述。")

    if hard_invalid:
        item["invalid_name"] = True
        item["name_validation_status"] = "invalid"
        item["invalid_name_reasons"] = list(dict.fromkeys(invalid_reasons))
        issues.append(f"{name} 非法材料名: {'；'.join(item['invalid_name_reasons'])}")
        return item, issues, True

    if not (t3_supported or kb_supported or ending_supported):
        item["needs_name_review"] = True
        item["name_validation_status"] = "needs_review"
        issues.append(f"{name} 未命中T3/知识库/有效材料结尾词，作为待复核候选保留。")
    else:
        item["name_validation_status"] = "valid"
    item["name_support"] = {
        "t3": t3_supported,
        "kb": kb_supported,
        "ending": ending_supported,
    }
    return item, issues, False


# ══════════════════════════════════════
# 规格型号格式校验（/check-spec-format skill 规则）
# ══════════════════════════════════════

SPEC_FORMAT_PATTERNS = [
    # F4: 公称参数型 — 必须DN+PN同时出现（DNxx PNxx），优先于单独DN
    (re.compile(r'^DN\d+\s+PN\d+'), 'F4', '公称参数'),
    # F2: 尺寸型 — 单独DN/φ/尺寸
    (re.compile(r'\d+\s*[×xX]\s*\d+(?:\s*[×xX]\s*\d+)?\s*mm'), 'F2', '截面尺寸'),
    (re.compile(r'^DN\d+$'), 'F2', '公称直径'),
    (re.compile(r'^[Φφ]\d+(?:\.\d+)?'), 'F2', '直径'),
    # F1: 强度/牌号型 — 纯强度值，不含其他参数
    (re.compile(r'^C\d{2,3}$'), 'F1', '混凝土强度'),
    (re.compile(r'^HRB\d+E?$|^HPB\d+$'), 'F1', '钢筋牌号'),
    (re.compile(r'^Q\d{3}[A-Z]?$'), 'F1', '钢材牌号'),
    (re.compile(r'^MU\d+$'), 'F1', '砌体强度'),
    (re.compile(r'^M\d+(?:\.\d+)?$'), 'F1', '砂浆强度'),
    # F3: 型号编码型（电缆/管线编码可含 - / . kV 等符号）
    (re.compile(r'^[A-Z]{2,}[-\s]?\d'), 'F3', '型号编码'),
    (re.compile(r'^[A-Z]{2,}[-/]YJV|^YJV|^NH-|^ZR-|^WDZ-'), 'F3', '电缆型号'),
    # F6: 厚度/面积型
    (re.compile(r'^\d+(?:\.\d+)?\s*mm$'), 'F6', '厚度'),
    (re.compile(r'^\d+(?:\.\d+)?\s*m\s*[×xX]\s*\d+(?:\.\d+)?\s*m$'), 'F6', '面积'),
]

SPEC_FORBIDDEN_PATTERNS = [
    (re.compile(r'详见|参照|参见|见设计|由厂家|厂家提供|其他参数'), '引用词/放弃型占位词'),
    (re.compile(r'投标|自报|报价'), '投标用语'),
    (re.compile(r'费\b'), '费用后缀（除服务器外）'),
]


def classify_spec_format(spec_text: str) -> tuple[str, str]:
    """S1: 将规格文本归入六种格式。返回 (format_type, label)。
    复合描述型(F5)：含多个独立参数块（被空格分隔 ≥2），且不属单一 F1/F2/F4 纯格式。
    """
    if not spec_text or not spec_text.strip():
        return ("F0", "空规格")
    text = spec_text.strip()
    parts = text.split()
    # 先尝试单一格式匹配
    for pattern, ftype, label in SPEC_FORMAT_PATTERNS:
        if pattern.search(text):
            # 单一格式匹配成功，但若存在多个独立参数块，升级为 F5
            if len(parts) >= 2 and ftype in ("F1", "F2"):
                # 检查其余部分是否属于同类型；若混合不同类型参数则升级
                other_parts = [p for p in parts if not pattern.search(p)]
                if other_parts:
                    return ("F5", "复合描述型")
            return (ftype, label)
    if len(parts) >= 2:
        return ("F5", "复合描述型")
    # 未命中任何模式，但仍可能有描述性文本
    return ("F5", "复合描述型")


def validate_spec_format(material_name: str, spec: str) -> dict:
    """S0-S5: 对 name+spec 执行完整规格格式校验。"""
    result = {
        "s0_no_name_in_spec": {"pass": True, "issue": None},
        "s1_format_type": {"type": "F0", "label": ""},
        "s2_param_order": {"pass": True, "issue": None},
        "s3_kv_parsed": {"applicable": False, "result": None},
        "s5_no_forbidden": {"pass": True, "issues": []},
        "issues": [],
    }

    name_compact = compact_text(material_name or "")
    spec_compact = compact_text(spec or "")
    if not spec_compact:
        result["s1_format_type"] = {"type": "F0", "label": "空规格"}
        return result

    # S0: 规格字段禁止包含材料名
    if name_compact and name_compact in spec_compact:
        result["s0_no_name_in_spec"] = {"pass": False, "issue": f"规格中重复了材料名「{name_compact}」"}
        result["issues"].append("s0: 规格含材料名")

    # S1: 六种格式识别
    ftype, flabel = classify_spec_format(spec_compact)
    result["s1_format_type"] = {"type": ftype, "label": flabel}

    # S3: 中建 KV 格式检测
    if "," in spec and ":" in spec:
        kv_pairs = []
        parts = spec.split(",")
        for p in parts[1:]:
            if ":" in p:
                k, _, v = p.partition(":")
                kv_pairs.append({"key": k.strip(), "value": v.strip()})
        if kv_pairs:
            result["s3_kv_parsed"] = {"applicable": True, "pairs": kv_pairs}

    # S5: 禁止事项检查
    for pattern, reason in SPEC_FORBIDDEN_PATTERNS:
        if pattern.search(spec):
            result["s5_no_forbidden"]["pass"] = False
            result["s5_no_forbidden"]["issues"].append(reason)
            result["issues"].append(f"s5: {reason}")

    return result


# ══════════════════════════════════════
# 材料名称+规格综合校验（/normalize-material skill 规则）
# ══════════════════════════════════════

def normalize_material(item: dict, kb_context: dict) -> dict:
    """综合标准化：名称校验 + 规格校验，输出完整的标准化结果和置信度。"""
    # Step 1: 规格分离 + 名称校验
    item, issues, invalid = preprocess_material_candidate(item, kb_context)
    # Step 2: 规格格式校验
    spec = str(item.get("spec_hint", "") or item.get("spec", ""))
    spec_result = validate_spec_format(item.get("material_name", ""), spec)
    # Step 3: 综合判定
    name_status = item.get("name_validation_status", "valid")
    name_level = classify_name_level(item.get("material_name", ""))
    item["name_level"] = name_level

    # 决定 confidence 和 needs_review
    if name_status == "invalid" or not spec_result["s0_no_name_in_spec"]["pass"]:
        confidence, needs_review = "low", True
    elif name_status == "needs_review" or spec_result["issues"]:
        confidence, needs_review = "medium", True
    else:
        confidence, needs_review = "high", False

    item["name_spec_validation"] = {
        "name_checks": {
            "r0_pass": name_status == "valid",
            "r1_pass": not item.get("invalid_name"),
            "r2_pass": item.get("name_level", "") in ("L1", "L2", "L3", "L4"),
            "r3_pass": bool(item.get("name_support", {}).get("ending")),
            "r4_pass": not item.get("invalid_name"),
            "r5_pass": not item.get("invalid_name"),
            "r6_level": name_level,
            "r8_pass": not item.get("is_fee_item", False),
        },
        "spec_checks": spec_result,
        "confidence": confidence,
        "needs_review": needs_review,
    }
    item["confidence"] = confidence
    item["needs_review"] = needs_review
    return item


def classify_name_level(name: str) -> str:
    """R6: 五级命名结构分类。L5（系统/装置）优先判定，不受字数限制。
    结构优先于字数：如「XX用XX」结构即使≤9字也应归L3。"""
    name_str = str(name or "")
    # L5: 系统/装置级 — 优先检查
    if any(kw in name_str for kw in ("系统", "装置", "机组", "总成", "泵站", "一体化")):
        return "L5"
    # L4: 品牌/型号前缀 — 含字母编码
    if re.search(r'^[A-Z]{2,}', name_str):
        return "L4"
    # L3: 用途+材料结构 — 含「用」且不单纯是特性修饰
    if "用" in name_str and len(compact_text(name_str) or name_str) >= 6:
        return "L3"
    n = len(compact_text(name_str) or name_str)
    if n <= 4:
        return "L1"
    if n <= 9:  # 放宽到9字，容纳"聚合物水泥防水涂料"等L2常见长名
        return "L2"
    if n <= 12:
        return "L3"
    return "L4"


def preprocess_material_candidate(item: dict, kb_context: dict) -> tuple[dict, list[str], bool]:
    """Stage4 入口预处理：先拆规格，再校验材料名 + 规格格式。"""
    issues = []
    raw_name = str(item.get("material_name", "")).strip()
    if raw_name:
        item.setdefault("input_material_name", raw_name)
    core_name, spec_hint, specs = split_spec_from_name(raw_name, item.get("spec_hint", ""))
    if core_name != raw_name:
        item["material_name"] = core_name
        item["spec_hint"] = spec_hint
        item["extracted_specs"] = specs
        item["_name_before_spec_split"] = raw_name
        issues.append(f"{raw_name} 已拆分为材料名「{core_name}」与规格「{spec_hint}」。")
    elif spec_hint:
        item["spec_hint"] = spec_hint
    # 名称合法性校验
    item, name_issues, invalid = validate_material_name(item, kb_context)
    issues.extend(name_issues)
    # 规格格式校验（新增）
    current_spec = item.get("spec_hint", "") or item.get("spec", "")
    spec_result = validate_spec_format(item.get("material_name", ""), current_spec)
    item["spec_validation"] = spec_result
    if spec_result["issues"]:
        issues.append(f"规格校验: {'; '.join(spec_result['issues'])}")
    return item, issues, invalid


def load_project_defaults_kb() -> dict:
    """读取项目默认做法推荐库。该库只作缺参数推荐锚点，不直接生成采购材料。"""
    global PROJECT_DEFAULTS_KB_CACHE
    if PROJECT_DEFAULTS_KB_CACHE is not None:
        return PROJECT_DEFAULTS_KB_CACHE
    if not os.path.exists(PROJECT_DEFAULTS_KB_PATH):
        PROJECT_DEFAULTS_KB_CACHE = {}
        return PROJECT_DEFAULTS_KB_CACHE
    try:
        with open(PROJECT_DEFAULTS_KB_PATH, encoding="utf-8") as f:
            PROJECT_DEFAULTS_KB_CACHE = json.load(f)
    except Exception:
        PROJECT_DEFAULTS_KB_CACHE = {}
    return PROJECT_DEFAULTS_KB_CACHE


def infer_project_default_phase(boq_item: dict) -> str:
    """把 Q0 分部/清单名称/特征映射到 project_defaults_kb 的分部。"""
    text = compact_text(
        f"{boq_item.get('t1_section', '')} {boq_item.get('standard_name', '')} "
        f"{boq_item.get('name', '')} {boq_item.get('feature_text', '')}"
    )
    if any(k in text for k in ["混凝土窗台板", "现浇窗台板", "窗台压顶", "混凝土压顶"]) or (
        "混凝土" in text and any(k in text for k in ["窗台板", "压顶"])
    ):
        return "主体结构工程"
    checks = [
        ("保温工程", ["保温", "隔热", "XPS", "EPS", "挤塑", "岩棉"]),
        ("防水工程", ["防水", "止水", "SBS", "卷材", "涂膜", "抗渗", "嵌缝"]),
        ("门窗工程", ["门窗", "金属门", "金属窗", "铝合金窗", "防火门", "窗"]),
        ("外立面装饰", ["外立面", "幕墙", "真石漆", "外墙涂料", "干挂", "一体板"]),
        ("精装修工程", ["精装修", "吊顶", "饰面", "瓷砖", "地砖", "木地板", "壁纸"]),
        ("粗装修工程", ["抹灰", "找平", "粗装修", "粉刷", "腻子", "楼地面"]),
        ("主体结构工程", ["混凝土", "钢筋", "柱", "梁", "板", "墙", "砌体", "砖", "钢结构", "主体"]),
    ]
    for phase, keywords in checks:
        if any(compact_text(k) in text for k in keywords):
            return phase
    return boq_item.get("t1_section", "") or ""


def infer_project_default_locations(boq_item: dict) -> list[str]:
    text = compact_text(
        f"{boq_item.get('standard_name', '')} {boq_item.get('name', '')} {boq_item.get('feature_text', '')}"
    )
    mapping = [
        ("地下车库", ["地下车库", "车库"]),
        ("地下室", ["地下室", "地下", "底板", "外墙防水"]),
        ("屋面", ["屋面", "屋顶"]),
        ("外墙", ["外墙", "外立面", "外保温"]),
        ("内墙", ["内墙", "墙面"]),
        ("楼地面", ["楼地面", "地面", "散水", "坡道"]),
        ("天棚", ["天棚", "吊顶", "顶棚"]),
        ("厨卫", ["厨房", "卫生间", "厨卫"]),
        ("阳台", ["阳台", "露台"]),
        ("基础", ["基础", "底板", "承台"]),
        ("门窗洞口", ["门窗洞口", "洞口"]),
        ("窗台", ["窗台", "窗台板", "窗台压顶"]),
        ("压顶", ["压顶", "扶手压顶"]),
    ]
    result = []
    for location, keywords in mapping:
        if any(compact_text(k) in text for k in keywords):
            result.append(location)
    return result


def keyword_list_matches(text: str, keywords_all: list | None = None, keywords_any: list | None = None) -> bool:
    compact = compact_text(text)
    keywords_all = keywords_all or []
    keywords_any = keywords_any or []
    if keywords_all and not all(compact_text(k) in compact for k in keywords_all if k):
        return False
    if keywords_any and not any(compact_text(k) in compact for k in keywords_any if k):
        return False
    return True


def boq_opening_kind(boq_item: dict) -> str:
    text = compact_text(f"{boq_item.get('standard_name', '')} {boq_item.get('name', '')} {boq_item.get('feature_text', '')}")
    has_window = "窗" in text
    has_door = "门" in text
    if has_window and not has_door:
        return "window"
    if has_door and not has_window:
        return "door"
    if "窗代号" in text or "玻璃品种" in text or "纱窗" in text:
        return "window"
    if "门代号" in text or "门框" in text or "门扇" in text:
        return "door"
    return ""


def project_default_rec_matches_boq(rec: dict, boq_item: dict) -> bool:
    """默认推荐材料必须贴合当前清单对象，避免同一分部下兄弟体系串入。"""
    name = compact_text(rec.get("material_name", ""))
    text = compact_text(f"{boq_item.get('standard_name', '')} {boq_item.get('name', '')} {boq_item.get('feature_text', '')}")
    kind = boq_opening_kind(boq_item)
    if kind == "window" and "门" in name and "窗" not in name:
        return False
    if kind == "door" and "窗" in name and "门" not in name:
        return False
    if "塑钢" in name and any(x in text for x in ["断桥", "断热桥", "铝合金"]):
        return "塑钢" in text
    if "铝包木" in name:
        return "铝包木" in text
    if "系统窗" in name:
        return "系统窗" in text
    if "铜门" in name or "铸铝门" in name:
        return any(x in text for x in ["铜门", "铸铝门"])
    # 设备类型专属过滤：避免无关设备材料串入
    DEVICE_EXCLUSIVE = {
        "净化设备":       ["净化", "洁净", "洁净室", "洁净间"],
        "人防过滤吸收器":  ["人防", "防护", "人员掩蔽"],
        "人防过滤":       ["人防", "防护"],
        "空气加热器":     ["加热器", "空气加热", "新风加热"],
        "空气冷却器":     ["冷却器", "空气冷却"],
        "空气过滤器":     ["过滤", "净化", "洁净", "空调箱", "过滤器", "过滤箱"],
    }
    for prefix, required_kws in DEVICE_EXCLUSIVE.items():
        if prefix in name:
            if not any(compact_text(kw) in text for kw in required_kws):
                return False
    return True


def project_default_recommendations_by_system(kb: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for rec in kb.get("material_recommendations", []) or []:
        grouped.setdefault(rec.get("system_id", ""), []).append(rec)
    return grouped


def compact_project_default_recommendation(rec: dict) -> dict:
    return {
        "rec_id": rec.get("rec_id", ""),
        "system_id": rec.get("system_id", ""),
        "material_name": rec.get("material_name", ""),
        "role": rec.get("role", ""),
        "typical_spec": rec.get("typical_spec", ""),
        "spec_params": rec.get("spec_params", {}),
        "unit_hint": rec.get("unit_hint", ""),
        "auxiliary_group": rec.get("auxiliary_group", []),
        "basis": rec.get("basis", []),
        "loss_rate_hint": rec.get("loss_rate_hint"),
        "quantity_rule": rec.get("quantity_rule", {}),
        "default_spec_values": rec.get("default_spec_values", {}),
        "required_params": rec.get("required_params", []),
        "source_type": "project_default_kb",  # 统一归一化，含 AI 候选
        "confidence": rec.get("confidence", "medium"),
        "can_auto_add": rec.get("can_auto_add", False),
        "requires_review_if_not_in_feature": rec.get("requires_review_if_not_in_feature", True),
        "notes": rec.get("notes", ""),
    }


def retrieve_project_defaults(boq_item: dict, L1: dict | None = None) -> dict:
    """查询 project_defaults_kb，返回 prompt 推荐上下文；不直接生成最终材料。"""
    kb = load_project_defaults_kb()
    if not kb:
        return {"matched_defaults": [], "prompt_injection": "", "risk_notes": ["project_defaults_kb 未加载。"]}

    L1 = L1 or {}
    phase = infer_project_default_phase(boq_item)
    feature_text = f"{boq_item.get('name', '')} {boq_item.get('standard_name', '')} {boq_item.get('feature_text', '')}"
    query = {
        "project_category": L1.get("project_category") or L1.get("project_type") or "",
        "structure_type": L1.get("structure_type", ""),
        "height_scope": L1.get("height_scope", ""),
        "phase": phase,
        "locations": infer_project_default_locations(boq_item),
    }
    recs_by_system = project_default_recommendations_by_system(kb)
    systems_by_id = {s.get("system_id", ""): s for s in kb.get("default_systems", []) or []}
    phase_cfg = (kb.get("phase_matrix", {}) or {}).get(phase, {})
    risk_notes = []
    if phase_cfg and phase_cfg.get("include") is False:
        return {
            "query": query,
            "matched_defaults": [],
            "prompt_injection": "",
            "risk_notes": [phase_cfg.get("reason", f"{phase} 不适合使用默认推荐库。")],
        }

    matched: dict[str, dict] = {}

    def add_system(system_id: str, score: int, match_level: str, trigger_id: str = ""):
        if not system_id or system_id not in systems_by_id:
            return
        system = systems_by_id[system_id]
        existing = matched.get(system_id)
        if existing and existing["priority"] >= score:
            return
        matched[system_id] = {
            "system": system,
            "priority": score,
            "match_level": match_level,
            "trigger_id": trigger_id,
        }

    # 1) 特征触发最优先。
    for trigger in kb.get("trigger_rules", []) or []:
        trigger_phase = trigger.get("phase", "")
        if trigger_phase and phase and trigger_phase != phase:
            continue
        if not keyword_list_matches(feature_text, trigger.get("keywords_all", []), trigger.get("keywords_any", [])):
            continue
        loc_keywords = trigger.get("location_keywords", []) or []
        location_matched = not loc_keywords or any(compact_text(k) in compact_text(feature_text) for k in loc_keywords)
        if not location_matched and query["locations"]:
            location_matched = bool(set(loc_keywords) & set(query["locations"]))
        if not location_matched and "外墙" in loc_keywords and "墙面" in compact_text(feature_text):
            location_matched = True
        if not location_matched:
            continue
        location_bonus = 5 if loc_keywords else 0
        add_system(
            trigger.get("target_system_id", ""),
            int(trigger.get("priority", 80)) + location_bonus,
            "feature_trigger",
            trigger.get("trigger_id", ""),
        )

    # 1.5) Section-code 精确匹配（AI生成的默认材料体系）。
    # 当 BOQ item 有已知 Q0 分部编码时，直接命中 AI 为该分部生成的默认材料推荐。
    boq_section_code = boq_item.get("section_code", "") or ""
    if not boq_section_code and boq_item.get("standard_code"):
        sc = str(boq_item.get("standard_code", ""))
        if len(sc) >= 6:
            boq_section_code = sc[:6]
    if boq_section_code:
        candidates_sec = {boq_section_code}
        if len(boq_section_code) == 6:
            candidates_sec.add(boq_section_code + "0")  # e.g. "030401" → also try "0304010"
        for system in kb.get("default_systems", []) or []:
            if system.get("section_code", "") not in candidates_sec:
                continue
            if system.get("source_type") != "project_default_kb_ai_candidate":
                continue
            sid = system.get("system_id", "")
            if not sid or sid in matched:
                continue
            score = 70
            if query["project_category"] and query["project_category"] in (system.get("applicable_project_category", []) or []):
                score += 10
            if query["locations"] and set(query["locations"]) & set(system.get("applicable_location", []) or []):
                score += 5
            add_system(sid, score, "section_code_match")

    # 2) 维度匹配。若已有特征触发，保温/防水等部位敏感分部不再展开其他默认体系，避免屋面/外墙等做法串用。
    structure_sensitive = "structure_type" in (phase_cfg.get("primary_dimensions", []) or [])
    if structure_sensitive and not query["structure_type"]:
        risk_notes.append(f"{phase or '当前分部'} 缺少 structure_type，默认库不展开结构体系材料，只保留特征触发推荐。")

    has_feature_trigger = any(x.get("match_level") == "feature_trigger" for x in matched.values())
    for system in kb.get("default_systems", []) or []:
        if phase and system.get("phase") != phase:
            continue
        if has_feature_trigger and phase in {"保温工程", "防水工程", "门窗工程", "外立面装饰"}:
            if system.get("system_id", "") not in matched:
                continue
        if structure_sensitive and not query["structure_type"]:
            continue
        score = 40
        if query["project_category"] and query["project_category"] in (system.get("applicable_project_category", []) or []):
            score += 15
        elif query["project_category"] and system.get("applicable_project_category"):
            continue
        if query["structure_type"] and query["structure_type"] in (system.get("applicable_structure_type", []) or []):
            score += 25
        elif query["structure_type"] and system.get("applicable_structure_type"):
            continue
        if query["height_scope"] and query["height_scope"] in (system.get("applicable_height_scope", []) or []):
            score += 20
        elif query["height_scope"] and system.get("applicable_height_scope"):
            continue
        sys_locations = system.get("applicable_location", []) or []
        if query["locations"] and sys_locations and set(query["locations"]) & set(sys_locations):
            score += 15
        elif query["locations"] and sys_locations and system.get("phase") in {"防水工程", "保温工程", "门窗工程"}:
            continue
        add_system(system.get("system_id", ""), score, "dimension_match")

    # 3) 排除规则。
    excluded_systems, excluded_materials, exclusion_notes = set(), set(), []
    for rule in kb.get("exclusion_rules", []) or []:
        if not any(compact_text(k) in compact_text(feature_text) for k in rule.get("if_feature_contains", []) or []):
            continue
        excluded_systems.update(rule.get("exclude_systems", []) or [])
        excluded_materials.update(compact_text(x) for x in rule.get("exclude_materials", []) or [])
        exclusion_notes.append(rule.get("reason", "命中特征排除规则。"))

    max_recs = int((kb.get("retrieval_policy", {}) or {}).get("max_recommendations_per_phase", 8))
    rows = []
    total_recs = 0
    for system_id, item in sorted(matched.items(), key=lambda x: -x[1]["priority"]):
        if system_id in excluded_systems:
            continue
        rec_rows = []
        for rec in recs_by_system.get(system_id, []):
            if not project_default_rec_matches_boq(rec, boq_item):
                continue
            if compact_text(rec.get("material_name", "")) in excluded_materials:
                continue
            rec_rows.append(compact_project_default_recommendation(rec))
        if not rec_rows:
            # 预留体系不注入材料，避免 AI 自行扩散。
            continue
        if total_recs >= max_recs:
            break
        take = rec_rows[: max(0, max_recs - total_recs)]
        total_recs += len(take)
        system = item["system"]
        rows.append({
            "system_id": system_id,
            "system_name": system.get("system_name", ""),
            "phase": system.get("phase", ""),
            "typical_scene": system.get("typical_scene", ""),
            "process_steps": system.get("process_steps", []),
            "match_level": item["match_level"],
            "trigger_id": item.get("trigger_id", ""),
            "priority": item["priority"],
            "confidence": system.get("confidence", "medium"),
            "policy": {
                "must_yield_to_feature_text": system.get("must_yield_to_feature_text", True),
                "must_pass_t3": system.get("must_pass_t3", True),
                "can_auto_add_materials": system.get("can_auto_add_materials", False),
                "requires_review_if_not_in_feature": system.get("requires_review_if_not_in_feature", True),
            },
            "recommendations": take,
        })

    prompt_lines = []
    if rows:
        prompt_lines.append("【项目默认推荐库】以下内容只作缺参数时的常规做法锚点，不得覆盖项目特征、图纸、Q0/Q1/Q2/T3/N规范库。")
        prompt_lines.append("若采纳推荐材料，source_type 必须为 project_default_kb，confidence 不得高于 medium，且最终必须通过 T3 标准化和 Stage6 复核。")
        for row in rows[:4]:
            mats = "；".join(
                f"{r['material_name']}({r.get('typical_spec', '')})"
                for r in row.get("recommendations", [])[:6]
            )
            prompt_lines.append(f"- {row['system_name']}[{row['match_level']}]: {mats}")

    return {
        "query": query,
        "matched_defaults": rows,
        "prompt_injection": "\n".join(prompt_lines),
        "exclusion_notes": exclusion_notes,
        "risk_notes": risk_notes,
    }


def materials_from_project_defaults(project_defaults: dict, boq_item: dict | None = None) -> dict:
    """把默认推荐库命中的材料作为候选送入 Stage4；仍需 T3/Stage6 校验，不直接自动通过。"""
    rows, seen = [], set()
    for system in (project_defaults or {}).get("matched_defaults", []):
        for rec in system.get("recommendations", []):
            if boq_item and not project_default_rec_matches_boq(rec, boq_item):
                continue
            name = rec.get("material_name", "")
            if not name:
                continue
            key = material_identity_key(name)
            if key in seen:
                continue
            seen.add(key)
            # 从 boq_item 上下文中推导 spec_hint（输入中的强度等级、钢筋牌号等）
            derived_spec = ""
            if "混凝土" in name and boq_item.get("concrete_strength"):
                derived_spec = boq_item["concrete_strength"]
            elif "钢筋" in name and boq_item.get("steel_grade"):
                derived_spec = boq_item["steel_grade"]

            rows.append({
                "material_name": name,
                "spec_hint": derived_spec,
                "rec_id": rec.get("rec_id", ""),
                "project_default_system_id": system.get("system_id", ""),
                "project_default_system_name": system.get("system_name", ""),
                "project_default_typical_spec": rec.get("typical_spec", ""),
                "project_default_spec_params": rec.get("spec_params", {}),
                "project_default_basis": rec.get("basis", []),
                "quantity_rule": rec.get("quantity_rule", {}),
                "default_spec_values": rec.get("default_spec_values", {}),
                "required_params": rec.get("required_params", []),
                "loss_rate_hint": rec.get("loss_rate_hint"),
                "unit_hint": rec.get("unit_hint", ""),
                "unit": rec.get("unit_hint", ""),
                "role": rec.get("role", ""),
                "reason": f"项目默认推荐库命中 {system.get('system_name', '')}",
                "trigger_process": "project_defaults_kb",
                "evidence": system.get("match_level", ""),
                "confidence": "medium",
                "source": "project_default_kb",
                "source_type": "project_default_kb",
                "applicability": "默认推荐，需结合项目特征复核",
                "requires_review_if_not_in_feature": rec.get("requires_review_if_not_in_feature", True),
                "can_auto_add": False,
            })
    return {"materials": rows}


def iter_matched_project_default_recs(kb_context: dict) -> list[dict]:
    rows = []
    boq_item = kb_context.get("boq_item", {})
    for system in kb_context.get("project_defaults", {}).get("matched_defaults", []) or []:
        for rec in system.get("recommendations", []) or []:
            if boq_item and not project_default_rec_matches_boq(rec, boq_item):
                continue
            copied = dict(rec)
            copied.setdefault("system_id", system.get("system_id", ""))
            copied.setdefault("system_name", system.get("system_name", ""))
            rows.append(copied)
    return rows


def project_default_match_score(item: dict, rec: dict) -> int:
    names = [
        item.get("rec_id", ""),
        item.get("material_name", ""),
        item.get("raw_material_name", ""),
        item.get("input_material_name", ""),
        item.get("_name_before_spec_split", ""),
        item.get("t3_standard_name", ""),
        item.get("t3_name", ""),
    ]
    rec_id = rec.get("rec_id", "")
    if rec_id and rec_id in names:
        return 1000

    rec_name = compact_text(rec.get("material_name", ""))
    if not rec_name:
        return 0
    best = 0
    for name in names:
        norm = compact_text(name)
        if not norm:
            continue
        if norm == rec_name:
            best = max(best, 500)
        elif rec_name in norm or norm in rec_name:
            best = max(best, min(len(rec_name), len(norm)) * 10)
        elif "聚合物" in rec_name and "聚合物" in norm and "砂浆" in rec_name and "砂浆" in norm:
            best = max(best, 80)
        elif "网格布" in rec_name and "网格布" in norm:
            best = max(best, 80)
        elif ("挤塑" in rec_name or "xps" in rec_name.lower()) and ("挤塑" in norm or "xps" in norm.lower()):
            best = max(best, 80)
        elif ("粘接剂" in rec_name or "粘结剂" in rec_name) and ("粘接剂" in norm or "粘结剂" in norm or "胶粘剂" in norm):
            best = max(best, 80)
    return best


def project_default_t3_compatible(default_name: str, current_name: str) -> bool:
    default_compact = compact_text(default_name).lower()
    current_compact = compact_text(current_name).lower()
    if not default_compact or not current_compact:
        return True
    if default_compact in current_compact or current_compact in default_compact:
        return True
    shared = set(material_core_tokens(default_name)) & set(material_core_tokens(current_name))
    if shared:
        return True
    bridges = [
        (["挤塑", "xps"], ["挤塑", "xps", "聚苯乙烯"]),
        (["eps", "模塑"], ["eps", "模塑", "聚苯乙烯"]),
        (["岩棉"], ["岩棉"]),
        (["网格布", "玻纤"], ["网格布", "玻纤"]),
        (["砂浆"], ["砂浆"]),
        (["粘接剂", "粘结剂", "胶粘剂"], ["粘接剂", "粘结剂", "胶粘剂"]),
        (["保温锚栓"], ["保温锚栓"]),
        (["钢筋"], ["钢筋", "网片"]),
        (["模板", "胶合板"], ["模板", "胶合板", "木模板"]),
        (["混凝土", "商砼"], ["混凝土", "预拌", "商品", "商砼"]),
    ]
    for left_terms, right_terms in bridges:
        if any(t in default_compact for t in left_terms) and any(t in current_compact for t in right_terms):
            if "保温锚栓" in default_compact and "化学锚栓" in current_compact:
                return False
            return True
    return False


def attach_project_default_recommendation(item: dict, kb_context: dict) -> tuple[dict, list[str]]:
    """把命中的 project_defaults_kb 执行字段挂到材料上，供 Stage3 直接算量。"""
    issues = []
    if item.get("quantity_rule") and item.get("rec_id"):
        rec_name = item.get("input_material_name") or item.get("_name_before_spec_split") or item.get("raw_material_name", "")
        if item.get("source_type") == "project_default_kb" and rec_name and not project_default_t3_compatible(rec_name, item.get("material_name", "")):
            issues.append(
                f"{item.get('material_name')} 与默认推荐材料「{rec_name}」不一致，已回退为默认库材料名并标记复核。"
            )
            item["material_name"] = rec_name
            item["standardization_status"] = "unmatched"
            item["needs_name_review"] = True
        if item.get("source_type") == "project_default_kb":
            item["source"] = "project_default_kb"
            item["confidence"] = "medium" if item.get("confidence") in {"", "low", "high", None} else item.get("confidence", "medium")
        return item, issues

    best_rec, best_score = None, 0
    for rec in iter_matched_project_default_recs(kb_context):
        score = project_default_match_score(item, rec)
        if score > best_score:
            best_rec, best_score = rec, score

    if not best_rec or best_score < 20:
        return item, issues

    rec_name = best_rec.get("material_name", "")
    if item.get("source_type") == "project_default_kb" and not project_default_t3_compatible(rec_name, item.get("material_name", "")):
        issues.append(
            f"{item.get('material_name')} 与默认推荐材料「{rec_name}」不一致，已回退为默认库材料名并标记复核。"
        )
        item["material_name"] = rec_name
        item["standardization_status"] = "unmatched"
        item["standardization_score"] = item.get("standardization_score", 0)
        item["needs_name_review"] = True

    item["rec_id"] = best_rec.get("rec_id", item.get("rec_id", ""))
    item["project_default_system_id"] = best_rec.get("system_id", item.get("project_default_system_id", ""))
    item["project_default_system_name"] = best_rec.get("system_name", item.get("project_default_system_name", ""))
    item["project_default_typical_spec"] = best_rec.get("typical_spec", item.get("project_default_typical_spec", ""))
    item["project_default_spec_params"] = best_rec.get("spec_params", item.get("project_default_spec_params", {}))
    item["project_default_basis"] = best_rec.get("basis", item.get("project_default_basis", []))
    if item.get("project_default_basis") and not item.get("standard_refs"):
        item["standard_refs"] = [
            {"standard_id": x, "relation": "project_defaults_kb"}
            for x in item.get("project_default_basis", [])
            if x
        ][:6]
        if item.get("standard_refs"):
            item["needs_standard_review"] = False
    item["quantity_rule"] = best_rec.get("quantity_rule", item.get("quantity_rule", {})) or {}
    item["default_spec_values"] = best_rec.get("default_spec_values", item.get("default_spec_values", {})) or {}
    item["required_params"] = best_rec.get("required_params", item.get("required_params", [])) or []
    if item.get("loss_rate_hint") is None:
        item["loss_rate_hint"] = best_rec.get("loss_rate_hint")
    if not item.get("unit") and best_rec.get("unit_hint"):
        item["unit"] = best_rec.get("unit_hint")

    item["source_type"] = "project_default_kb"
    item["source"] = "project_default_kb"
    item["confidence"] = "medium" if item.get("confidence") in {"", "low", "high", None} else item.get("confidence", "medium")
    item.setdefault("requires_review_if_not_in_feature", best_rec.get("requires_review_if_not_in_feature", True))
    issues.append(f"{item.get('material_name')} 已绑定项目默认推荐规则 {item.get('rec_id')}，Stage3 将按 quantity_rule 算量。")
    return item, issues


def material_supported_by_current_context(item: dict, kb_context: dict) -> bool:
    """判断低可信候选是否被项目特征、默认推荐或规则支撑。"""
    if item.get("source_type") == "project_default_kb":
        return True
    if item.get("mapping_rule_id"):
        return True
    boq_item = kb_context.get("boq_item", {})
    actual_text = normalize_material_match_text(
        f"{boq_item.get('name', '')} {boq_item.get('standard_name', '')} {boq_item.get('feature_text', '')}"
    )
    default_text_parts = []
    for system in kb_context.get("project_defaults", {}).get("matched_defaults", []):
        for rec in system.get("recommendations", []):
            default_text_parts.append(rec.get("material_name", ""))
            default_text_parts.append(rec.get("typical_spec", ""))
            default_text_parts.extend(rec.get("auxiliary_group", []) or [])
        for step in system.get("process_steps", []) or []:
            if isinstance(step, dict):
                default_text_parts.append(step.get("name", ""))
                default_text_parts.extend(step.get("typical_materials", []) or [])
    process_text = normalize_material_match_text(" ".join(
        [r.get("process_name", "") + " " + " ".join(r.get("materials_hint", [])) for r in kb_context.get("process_rules", [])]
    ))
    default_text = normalize_material_match_text(" ".join(default_text_parts))
    rule_text = normalize_material_match_text(" ".join(
        [r.get("material_name", "") + " " + " ".join(r.get("aliases", [])) for r in kb_context.get("material_rules", [])]
    ))
    support_text = f"{actual_text} {default_text} {process_text} {rule_text}"
    names = [
        item.get("material_name", ""),
        item.get("raw_material_name", ""),
        item.get("input_material_name", ""),
        item.get("t3_standard_name", ""),
    ]
    for name in names:
        norm_name = normalize_material_match_text(name)
        if not norm_name:
            continue
        if norm_name in support_text:
            return True
        tokens = [t for t in material_core_tokens(norm_name) if not is_generic_match_term(t)]
        if any(t and t in support_text for t in tokens):
            return True
    name_text = normalize_material_match_text(" ".join(names))
    special_pairs = [
        ("挤塑", ["挤塑", "XPS"]),
        ("聚苯", ["聚苯", "挤塑板", "XPS", "EPS"]),
        ("网格布", ["网格布", "玻纤"]),
        ("砂浆", ["聚合物砂浆", "聚合物水泥砂浆", "抗裂砂浆"]),
        ("粘接剂", ["粘接剂", "粘结剂", "胶粘剂"]),
        ("锚栓", ["锚栓"]),
        ("钢筋", ["钢筋", "网片", "绑扎"]),
        ("绑扎丝", ["绑扎丝", "钢筋绑扎", "扎丝"]),
        ("模板", ["模板", "支模", "木胶合板"]),
        ("脱模剂", ["脱模剂", "模板"]),
        ("垫块", ["保护层垫块", "钢筋保护层"]),
    ]
    for material_key, support_keys in special_pairs:
        if material_key in name_text and any(k in support_text for k in support_keys):
            return True
    return False


def score_standard_row(row: dict, name: str, feature_text: str) -> int:
    hay = compact_text(
        row.get("项目名称", "")
        + row.get("项目特征", "")
        + row.get("工作内容", "")
        + row.get("分部名称", "")
    )
    needle = compact_text(f"{name} {feature_text}")
    score = 0
    row_name = compact_text(row.get("项目名称", ""))
    in_name = compact_text(name)
    if row_name and in_name:
        if row_name == in_name:
            score += 100
        elif row_name in in_name or in_name in row_name:
            score += 80
    if row_name and row_name in needle:
        # 项目特征里的“名称:xxx”通常比合并名称更可信。
        score += 25 * needle.count(row_name)

    name_tokens = [x for x in re.split(r"[、，,（）()\\s]+", row_name + " " + in_name) if len(x) >= 2]
    for token in name_tokens:
        if token and token in row_name and token in in_name:
            score += 35
        elif token and (token in row_name or token in in_name) and token in hay and token in needle:
            score += 18

    for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9:：\\.]+", needle):
        if len(token) >= 2 and token in hay:
            score += min(len(token), 16)

    # 标准项边界词不能被忽略：实际清单没写钢筋/模板/装饰等，不能只因同属 0105 就错配。
    boundary_terms = ["钢筋", "模板", "螺栓", "铁件", "门窗", "装饰", "砌体", "石材", "砖"]
    for term in boundary_terms:
        if term in hay and term not in needle:
            score -= 70
        if term in needle and term in hay:
            score += 30

    # 单位是强约束。面积清单不应匹配到钢筋吨位清单。
    unit_label = re.search(r"计量单位[:：]\s*(m²|m2|㎡|平方米|m³|m3|立方米|t|吨|kg|m|米|个|项)", needle, re.I)
    actual_unit_match = unit_label or re.search(r"(m²|m2|㎡|平方米|m³|m3|立方米|t|吨|kg|(?<!m)m(?!m)|米|个|项)", needle, re.I)
    actual_unit = actual_unit_match.group(1) if actual_unit_match else ""
    standard_unit = row.get("计量单位", "")
    if actual_unit and standard_unit:
        if units_compatible(actual_unit, standard_unit):
            score += 40
        else:
            score -= 90

    return score


def lookup_q0_standard(code: str, name: str = "", feature_text: str = "") -> dict | None:
    """用 Q0 国标清单表做多级编码命中，并返回命中轨迹。"""
    rows = standard_q0_rows()
    if not rows:
        return None

    original_len = len(normalize_code(code))
    trace = []
    for cand in code_fallback_candidates(code):
        c, n = cand["code"], cand["length"]
        if n >= 9:
            matched = [r for r in rows if normalize_code(r.get("项目编码", "")) == c]
            match_type = "项目编码完全命中" if n == original_len else "回退到国标项目编码"
        elif n == 6:
            matched = [r for r in rows if normalize_code(r.get("分部编码", "")) == c]
            match_type = "回退到分部编码"
        elif n == 4:
            matched = [
                r for r in rows
                if normalize_code(r.get("分部编码", "")).startswith(c)
                or normalize_code(r.get("项目编码", "")).startswith(c)
            ]
            match_type = "回退到章节/分部前缀"
        else:
            matched = [r for r in rows if normalize_code(r.get("专业工程代码", "")) == c]
            match_type = "回退到专业工程代码"

        trace.append({"try_code": c, "level": cand["level"], "match_count": len(matched)})
        if not matched:
            continue

        ranked = sorted(
            matched,
            key=lambda r: score_standard_row(r, name, feature_text),
            reverse=True,
        )
        best = ranked[0]
        best_score = score_standard_row(best, name, feature_text)
        confidence = "high"
        if n < 9 and best_score >= 120:
            confidence = "high"
        elif n < 9 and best_score < 70:
            confidence = "low"
        elif n < 9 or best_score < 90:
            confidence = "medium"
        top_candidates = [
            {
                "项目编码": r.get("项目编码", ""),
                "项目名称": compact_text(r.get("项目名称", "")),
                "分部编码": r.get("分部编码", ""),
                "分部名称": r.get("分部名称", ""),
                "计量单位": r.get("计量单位", ""),
                "score": score_standard_row(r, name, feature_text),
            }
            for r in ranked[:8]
        ]
        return {
            "row": best,
            "match_type": match_type,
            "matched_code": c,
            "matched_level": cand["level"],
            "candidate_count": len(matched),
            "score": best_score,
            "confidence": confidence,
            "top_candidates": top_candidates,
            "trace": trace,
        }
    return {"row": None, "match_type": "未命中", "matched_code": "", "matched_level": "", "candidate_count": 0, "trace": trace}


def compare_standard_features(standard_features: list[str], actual_text: str) -> dict:
    actual_items = split_numbered_items(actual_text)
    actual_compact = compact_text(actual_text)
    matched, missing = [], []
    important_terms = [
        "保温隔热", "保温", "隔热", "部位", "厚度", "龙骨", "防护",
        "隔汽层", "强度", "材质", "规格", "粘结", "界面", "防水",
    ]
    for expected in standard_features:
        expected_compact = compact_text(expected)
        terms = [t for t in important_terms if t in expected_compact]
        ok = expected_compact and expected_compact in actual_compact
        if not ok and terms:
            ok = all(t in actual_compact for t in terms[:2]) or any(
                t in actual_compact for t in terms if t not in {"材料", "规格"}
            )
        if ok:
            matched.append(expected)
        else:
            missing.append(expected)

    extra = []
    for item in actual_items:
        item_compact = compact_text(item)
        if item_compact and not any(item_compact in compact_text(x) or compact_text(x) in item_compact for x in standard_features):
            extra.append(item)
    return {"matched": matched, "missing_expected": missing, "extra_input": extra[:12]}


def extract_material_hints(text: str) -> list[str]:
    """从文本中提取材料名 — 使用 T3 动态词库，不再维护硬编码关键词列表。"""
    return extract_materials_by_lexicon(text)


def clean_material_phrase(text: str) -> str:
    text = str(text or "").strip()
    text = text.replace("：", ":")
    text = re.sub(r"^\d+\s*[.)、]\s*", "", text)
    text = re.sub(r"^[,，;；、:：\s]+", "", text)
    # 剥离结构化字段前缀：名称:xxx → xxx
    text = re.sub(
        r"^(?:材料品种|材料名称|材料种类|混凝土种类|混凝土强度等级|"
        r"名称|种类|强度等级|等级|断面尺寸|品种|型号|规格)"
        r"\s*[:=]\s*",
        "", text,
    )
    text = re.sub(r"^(与墙体接触设|散水设分隔缝|分隔缝|设|撒)", "", text)
    ratio = re.search(r"(\d+\s*:\s*\d+\s*(?:灰土|水泥砂子|水泥砂浆|砂浆))", text)
    if ratio:
        return re.sub(r"\s+", "", ratio.group(1))
    concrete = re.search(r"(C\d+\s*[\u4e00-\u9fffA-Za-z0-9]*?混凝土)", text, re.I)
    if concrete:
        return re.sub(r"\s+", "", concrete.group(1))
    thick = re.search(r"\d+(?:\.\d+)?\s*(?:mm|cm|m)?\s*厚\s*(.+)$", text, re.I)
    if thick:
        text = thick.group(1)
    if "密封膏" in text:
        return "密封膏"
    if "嵌缝膏" in text:
        return "嵌缝膏"
    if "素土" in text:
        return "素土"
    if "灰土" in text:
        return "灰土"
    if "水泥砂子" in text:
        return "水泥砂子"
    # 常见缩写/俗称 → 标准名
    if text in ("商砼", "商品砼", "商品混凝土"):
        return "预拌混凝土"
    text = re.sub(r"(面层|垫层|保温层|防水层|找平层|结合层|保护层|材料|部位|做法)$", "", text)
    text = re.sub(r"(散水|坡道|地面|墙面|屋面)$", "", text)
    text = re.sub(r"(压实赶光|综合考虑|夯实|铺设|粘贴|压入|嵌缝|填缝).*$", "", text)
    text = re.sub(r"(壁厚|厚度)\s*\d+(?:\.\d+)?\s*mm?.*$", "", text, flags=re.I)
    text = text.strip(" ,，;；、。")
    return text


def extract_explicit_materials_generic(text: str) -> list[str]:
    """从任意清单特征中抽取明示材料，不依赖某个固定清单类型。"""
    text = str(text or "")
    normalized = text.replace("：", ":")
    candidates = []

    def add(name: str):
        name = clean_material_phrase(name)
        if not name or len(name) < 2:
            return
        if name in {"名称", "做法", "其他", "材料", "部位", "厚度", "详见设计图纸及技术要求"}:
            return
        if any(x in name for x in ["壁厚", "厚度", "洞口尺寸", "窗代号"]):
            return
        # 过滤裸规格代号（C20, C30, M10 等不是材料名）
        if re.match(r"^[A-Z]\d{1,3}$", name):
            return
        if name not in candidates:
            candidates.append(name)

    material_suffixes = ["混凝土", "砂浆", "灰土", "素土", "密封膏", "嵌缝膏", "密封胶", "粘接剂", "粘结剂", "网格布", "挤塑板", "保温板", "界面剂", "石膏"]
    raw_segments = []
    for line in split_numbered_items(normalized) or normalized.splitlines():
        raw_segments.extend(x for x in re.split(r"[\n,，;；。]", line) if x.strip())
    for segment in raw_segments:
        compact_segment = compact_text(segment)
        if any(s in compact_segment for s in material_suffixes):
            add(segment)

    # 使用 T3 词库动态匹配标准名称/别名，不再维护固定材料清单。
    lexicon = build_material_lexicon_from_t3()

    # 1) 厚度/强度/配合比 + 任意中文/字母数字组合，后验证是否命中 T3 词库
    for m in re.finditer(
        rf"((?:\d+(?:\.\d+)?\s*(?:mm|cm|m)\s*厚?)?\s*(?:C\d+\s*)?(?:\d+\s*[:：]\s*\d+\s*)?[\u4e00-\u9fffA-Za-z0-9+\-]+)",
        normalized,
        re.I,
    ):
        phrase = m.group(1)
        phrase = re.split(r"[\n,，;；。()（）]", phrase)[-1]
        phrase_compact = compact_text(phrase)
        matched = False
        for kw, _ in lexicon:
            kw_compact = compact_text(kw)
            if kw_compact in phrase_compact:
                matched = True
                break
        if matched:
            add(phrase)

    # 2) 字段值：材料品种、名称、种类等。
    for m in re.finditer(r"(?:材料品种|材料名称|材料种类|名称|种类)\s*[:=]\s*([^\n,，;；。]+)", normalized):
        add(m.group(1))

    # 3) 嵌缝/密封/粘接等短语 — 从 T3 词库动态匹配
    for kw, _ in lexicon:
        if any(s in kw for s in ["密封", "嵌缝", "粘接", "粘结", "界面", "胶粘"]):
            for m in re.finditer(rf"([^\n,，;；。]{{0,12}}{re.escape(kw)})", normalized):
                add(m.group(1))
    return candidates


def extract_feature_spec_params(feature_text: str) -> str:
    """从项目特征文本中提取规格参数片段，覆盖设备类和土建类参数。"""
    text = str(feature_text or "")
    parts = []

    # 1) 优先从「材质、规格:」「规格:」段落提取
    spec_section = re.search(
        r'(?:材质[、，]?\s*规格|规格|技术参数)[：:]\s*([^\n]+?)(?:[；;]\s*(?:\d+\s*[.、)]|\n|$)|$)',
        text
    )
    if spec_section:
        parts.append(spec_section.group(1).strip())

    # 2) 设备/材料参数通用模式：数字+工程单位
    param_patterns = [
        r'(?:制冷|制热|冷却|加热|耗电|输入|输出)?[^\s,;，；：:]*?\d+(?:\.\d+)?\s*(?:kW|W|V|Hz|A|MPa|kPa|Pa)\b[^\s,;，；]*',
        r'噪声[^\s,;，；]*?\d+(?:\.\d+)?\s*dB[^\s,;，；]*',
        r'冷媒[^\s,;，；]*?[A-Z]\d+[A-Za-z]*',
        r'[A-Z]+\([A-Z]\)\s*[≥≤><=]\s*\d+(?:\.\d+)?',
        r'IP\d{2}',
        r'IPLV[^\s,;，；]*?\d+(?:\.\d+)?',
        r'风量[^\s,;，；]*?\d+(?:\.\d+)?',
        r'\d+(?:\.\d+)?\s*[~～]\s*\d+(?:\.\d+)?\s*[℃°C]',
        r'(?:COP|EER|SEER|HSPF|APF)[^\s,;，；]*?\d+(?:\.\d+)?',
        r'厚\s*\d+(?:\.\d+)?\s*mm',
        r'[Φφ]\d+(?:\.\d+)?',
        r'DN\d+(?:\.\d+)?',
        r'C\d{2,3}',
        r'HRB\d+E?|HPB\d+',
        r'\d+:\d+(?:\.\d+)?',
    ]
    seen_specs = set()
    for pattern in param_patterns:
        for m in re.finditer(pattern, text, re.I):
            val = m.group(0).strip()
            if val and val not in seen_specs and len(val) >= 2:
                seen_specs.add(val)
                parts.append(val)

    # 3) 冷媒代号 R410A/R32/R134a 等独立模式
    for m in re.finditer(r'\bR\d{2,3}[A-Za-z]?\b', text):
        val = m.group(0).strip()
        if val not in seen_specs:
            seen_specs.add(val)
            parts.append(val)

    return "；".join(parts) if parts else ""


def extract_feature_param_map(feature_text: str) -> dict:
    """把项目特征中的关键规格参数抽成结构化字段，供规范校验和展示使用。"""
    text = str(feature_text or "").replace("：", ":")
    params: dict[str, str] = {}

    def put(key: str, value: str):
        value = str(value or "").strip(" ，,；;。")
        if value and key not in params:
            params[key] = value

    named_patterns = [
        ("设备名称", r"(?:^|\n)\s*(?:\d+\s*[.、)]\s*)?名称\s*:\s*([^\n；;]+)"),
        ("服务区域", r"服务区域\s*:\s*([^\n；;]+)"),
        ("制冷量", r"制冷量\s*([≥≤><=]?\s*\d+(?:\.\d+)?\s*kW)"),
        ("制热量", r"制热量\s*([≥≤><=]?\s*\d+(?:\.\d+)?\s*kW)"),
        ("制冷耗电量", r"制冷耗电量\s*([≥≤><=]?\s*\d+(?:\.\d+)?\s*kW)"),
        ("制热耗电量", r"制热耗电量\s*([≥≤><=]?\s*\d+(?:\.\d+)?\s*kW)"),
        ("耗电量", r"耗电量\s*([≥≤><=]?\s*\d+(?:\.\d+)?\s*(?:kW|W))"),
        ("风量", r"风量\s*([^\n,，；;]+?(?:m3/min|m³/min|m/min3|m³/h|m3/h))"),
        ("静压", r"静压\s*([≥≤><=]?\s*\d+(?:\.\d+)?\s*Pa)"),
        ("电压", r"((?:220|380|660)\s*V)"),
        ("噪声", r"噪声\s*[:：]?\s*([^\n,，；;]*?\d+(?:\.\d+)?\s*dB\(A\))"),
        ("冷媒", r"冷媒\s*([A-Za-z]*\s*R\d{2,3}[A-Za-z]?)"),
        ("IPLV(C)", r"IPLV\(C\)\s*([≥≤><=]\s*\d+(?:\.\d+)?)"),
        ("APF", r"APF\s*([≥≤><=]\s*\d+(?:\.\d+)?)"),
        ("防护等级", r"\b(IP\d{2})\b"),
        ("规格型号", r"规格型号\s*[:：]\s*([^\n；;]+)"),
        ("管径", r"\b(DN\d+(?:\.\d+)?)\b"),
        ("直径", r"([Φφ]\d+(?:\.\d+)?)"),
        ("厚度", r"(?:厚度|厚)\s*[:：]?\s*(\d+(?:\.\d+)?\s*mm)"),
        ("强度等级", r"\b(C\d{2,3})\b"),
        ("钢筋牌号", r"\b(HRB\d+E?|HPB\d+)\b"),
    ]
    for key, pattern in named_patterns:
        for m in re.finditer(pattern, text, re.I):
            put(key, m.group(1))

    # “材质、规格”整段作为兜底完整技术参数，避免参数名未覆盖时丢失。
    m = re.search(
        r"(?:材质[、，]?\s*规格|规格|技术参数)\s*:\s*([^\n]+?)(?:[；;]\s*(?:\d+\s*[.、)]|\n|$)|$)",
        text,
        re.I,
    )
    if m:
        put("规格描述", m.group(1))
    return params


def format_feature_params(params: dict, max_items: int = 12) -> str:
    parts = []
    for key, value in (params or {}).items():
        if value:
            parts.append(f"{key}:{value}")
        if len(parts) >= max_items:
            break
    return "；".join(parts)


def is_integrated_hvac_equipment(boq_item: dict) -> bool:
    """整体式/成套空调设备，工作内容中的风机不应拆成独立风机采购。"""
    text = compact_text(f"{boq_item.get('name', '')} {boq_item.get('standard_name', '')} {boq_item.get('feature_text', '')}")
    return any(k in text for k in ["多联式空调室外机", "多联式空调室内机", "多联机", "VRF", "VRV", "空调室外机", "空调室内机", "风冷热泵机组", "空调机组", "分体式空调"])


def extract_main_equipment_name(boq_item: dict) -> str:
    """从清单名称或“名称:”字段提取主设备名称。"""
    name = str(boq_item.get("name", "") or "").strip()
    feature = str(boq_item.get("feature_text", "") or "")
    match = re.search(r"(?:^|\n)\s*(?:\d+\s*[.、)]\s*)?名称\s*[：:]\s*([^\n；;]+)", feature)
    feature_name = match.group(1).strip() if match else ""
    if feature_name and len(feature_name) >= len(name):
        return feature_name
    return name or feature_name


def build_materials_from_feature_hints(boq_item: dict) -> dict:
    """把实际项目特征中明写的材料先抽为候选，作为 AI 不能漏掉的强约束。"""
    text = boq_item.get("feature_text", "")
    # 提取特征文本中的规格参数，用于注入主材料 spec_hint
    feature_specs = extract_feature_spec_params(text)
    materials = []
    seen = set()

    main_equipment = extract_main_equipment_name(boq_item)
    if main_equipment and is_integrated_hvac_equipment(boq_item):
        materials.append({
            "material_name": main_equipment,
            "spec_hint": feature_specs,
            "reason": "项目特征明示的成套空调设备本体",
            "trigger_process": "实际清单特征抽取",
            "evidence": "来自清单名称/名称字段/规格参数字段",
            "confidence": "high",
            "source": "feature_explicit",
            "source_type": "feature_explicit",
            "applicability": "适用",
            "role": "主材",
            "category_l1": "通风空调设备",
            "category_l2": "多联式空调设备",
            "unit": boq_item.get("unit") or "台",
            "coefficient": 1.0,
            "waste_rate_estimate": 0.0,
            "loss_basis": "成套设备按清单台数采购，不另计材料损耗。",
            "supply": "乙供",
        })
        seen.add(material_identity_key(main_equipment))

    for name in extract_material_hints(text) + extract_explicit_materials_generic(text):
        if is_integrated_hvac_equipment(boq_item) and ("风机" in compact_text(name) or compact_text(name).endswith("安装")):
            continue
        compact_name = material_identity_key(name)
        # 过宽的泛化词如果已有更具体材料，就不单独加入；更具体的后来出现时替换泛化词。
        if any(compact_name != old and compact_name in old for old in seen):
            continue
        materials = [m for m in materials if not (material_identity_key(m.get("material_name", "")) in compact_name and material_identity_key(m.get("material_name", "")) != compact_name)]
        seen = {material_identity_key(m.get("material_name", "")) for m in materials}
        if compact_name in seen:
            continue
        seen.add(compact_name)
        # 对于主设备材料（名称与清单项名称接近），注入特征规格
        boq_name = boq_item.get("name", "").strip()
        is_main_equipment = bool(boq_name) and (
            compact_text(boq_name[:6]) in compact_name
            or compact_name in compact_text(boq_name)
        )
        materials.append({
            "material_name": name,
            "spec_hint": feature_specs if is_main_equipment else "",
            "reason": "项目特征明示该材料或材料线索",
            "trigger_process": "实际清单特征抽取",
            "evidence": "来自输入项目特征原文",
            "confidence": "high",
            "source_type": "feature_explicit",
            "applicability": "适用",
        })
    return {"materials": materials}


def merge_material_candidates(*objs: dict) -> dict:
    merged, seen = [], set()
    for obj in objs:
        for item in (obj or {}).get("materials", []):
            if isinstance(item, str):
                item = {"material_name": item}
            if not isinstance(item, dict):
                continue
            name = str(item.get("material_name", "")).strip()
            if not name:
                continue
            compact_name = material_identity_key(name)
            # 更具体的材料已经存在时，跳过泛化词；更具体的后来出现时，替换泛化词。
            if any(compact_name != old and compact_name in old for old in seen):
                continue
            merged = [x for x in merged if not (material_identity_key(x.get("material_name", "")) in compact_name and material_identity_key(x.get("material_name", "")) != compact_name)]
            seen = {material_identity_key(x.get("material_name", "")) for x in merged}
            if compact_name in seen:
                continue
            merged.append(item)
            seen.add(compact_name)
    return {"materials": merged}


def quota_context_for_standard(standard_code: str, name: str, feature_text: str, limit: int = 8) -> dict:
    q1_rows = [r for r in load_csv_rows("Q1_定额索引.csv") if normalize_code(r.get("boq_code", "")) == normalize_code(standard_code)]
    if not q1_rows:
        return {"quota_candidates": [], "quota_materials": []}

    input_text = compact_text(f"{name} {feature_text}")
    tokens = extract_material_hints(f"{name}\n{feature_text}") + split_numbered_items(feature_text)

    def quota_score(row: dict) -> int:
        hay = compact_text(row.get("project_name", "") + row.get("project_spec", "") + row.get("work_content", ""))
        score = 0
        if compact_text(name) and compact_text(name) in hay:
            score += 30
        for t in tokens:
            tc = compact_text(t)
            if tc and tc in hay:
                score += min(len(tc), 20)
        if "拆除" in row.get("chapter_name", "") and "拆除" not in input_text:
            score -= 30
        return score

    q1_ranked = sorted(q1_rows, key=quota_score, reverse=True)[:limit]
    quota_ids = {r.get("quota_id", "") for r in q1_ranked}
    q2 = []
    for r in load_csv_rows("Q2_定额材料消耗.csv"):
        if r.get("quota_id", "") not in quota_ids:
            continue
        if r.get("cost_type", "") in {"人工", "机械"}:
            continue
        mat_name = r.get("material_name_raw", "").strip()
        if not mat_name or "其他材料费" in mat_name:
            continue
        q2.append(r)

    material_seen, material_summary = set(), []
    for r in q2:
        key = (r.get("material_name_raw", ""), r.get("material_spec_raw", ""), r.get("unit", ""))
        if key in material_seen:
            continue
        material_seen.add(key)
        material_summary.append({
            "quota_id": r.get("quota_id", ""),
            "material_name": r.get("material_name_raw", ""),
            "spec": r.get("material_spec_raw", ""),
            "quantity": r.get("quantity", ""),
            "unit": r.get("unit", ""),
            "is_main_material": r.get("is_main_material", ""),
            "material_id": r.get("material_id", ""),
            "map_status": r.get("map_status", ""),
            "province": r.get("province", ""),
        })

    return {
        "quota_candidates": [
            {
                "quota_id": r.get("quota_id", ""),
                "province": r.get("province", ""),
                "quota_code": r.get("quota_code", ""),
                "project_name": r.get("project_name", ""),
                "project_spec": r.get("project_spec", ""),
                "unit": r.get("unit", ""),
                "chapter_name": r.get("chapter_name", ""),
                "score": quota_score(r),
            }
            for r in q1_ranked
        ],
        "quota_materials": material_summary[:40],
    }


# ----------- 标准清单识别 -----------
def detect_code_name_conflict(code: str, q0_row: dict, input_name: str) -> dict | None:
    """检测“编码命中但名称语义明显不一致”的风险。只做门禁提示，不替换Q0。"""
    q0_name = compact_text(q0_row.get("项目名称", ""))
    name = compact_text(input_name)
    if not q0_name or not name:
        return None
    if q0_name == name or q0_name in name or name in q0_name:
        return None
    q0_tokens = {x for x in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", q0_name) if len(x) >= 2}
    input_tokens = {x for x in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", name) if len(x) >= 2}
    overlap = q0_tokens & input_tokens
    if overlap:
        return None
    return {
        "q0_name": q0_row.get("项目名称", ""),
        "input_name": input_name,
        "message": (
            f"清单编码 {code} 在Q0中对应「{q0_row.get('项目名称', '')}」，"
            f"但输入名称为「{input_name}」，可能存在编码填写错误或范围不一致。"
        ),
    }


def infer_standard_boq_from_code(code: str, name: str = "", feature_text: str = "") -> dict:
    """根据清单编码识别标准分部分项。优先用 Q0 国标表，多级回退命中。"""
    code = str(code or "").strip()

    q0_hit = lookup_q0_standard(code, name, feature_text)
    if q0_hit and q0_hit.get("row"):
        row = q0_hit["row"]
        standard_features = split_numbered_items(row.get("项目特征", ""))
        standard_work_items = split_numbered_items(row.get("工作内容", ""))
        code_name_conflict = detect_code_name_conflict(code, row, name)
        return {
            "standard_code": row.get("项目编码", "").strip() or q0_hit.get("matched_code", ""),
            "standard_name": compact_text(row.get("项目名称", "")) or name,
            "t1_section": row.get("分部名称", ""),
            "section_code": row.get("分部编码", ""),
            "work_scope": "；".join(standard_work_items) or row.get("工作内容", ""),
            "component": row.get("项目名称", ""),
            "standard_features": standard_features,
            "standard_work_items": standard_work_items,
            "standard_unit": row.get("计量单位", ""),
            "calculation_rule": row.get("工程量计算规则", ""),
            "q0_match": {
                "match_type": q0_hit.get("match_type", ""),
                "matched_code": q0_hit.get("matched_code", ""),
                "matched_level": q0_hit.get("matched_level", ""),
                "candidate_count": q0_hit.get("candidate_count", 0),
                "score": q0_hit.get("score", 0),
                "confidence": q0_hit.get("confidence", "low"),
                "top_candidates": q0_hit.get("top_candidates", []),
                "trace": q0_hit.get("trace", []),
                "source": "Q0_国家标准清单项目编码",
            },
            "code_name_conflict": code_name_conflict,
        }
    # ══════════════════════════════════════
    # Q0 未命中：不再使用硬编码 standard_map 兜底。
    # 返回低置信结果，由 Stage6 门禁决定是否需要人工复核。
    # ══════════════════════════════════════
    top_candidates = q0_hit.get("top_candidates", []) if q0_hit else []
    return {
        "standard_code": code or "",
        "standard_name": name or "",
        "t1_section": "",
        "section_code": "",
        "work_scope": "未命中Q0标准清单，无法获取标准工作范围。",
        "component": "",
        "standard_features": [],
        "standard_work_items": [],
        "standard_unit": "",
        "calculation_rule": "",
        "q0_match": q0_hit if q0_hit else {
            "match_type": "none",
            "matched_code": "",
            "matched_level": "",
            "candidate_count": 0,
            "score": 0,
            "confidence": "none",
            "top_candidates": top_candidates,
            "trace": [],
            "source": "Q0未命中",
        },
        "warnings": [],
        "code_name_conflict": None,
    }


def build_feature_comparison(boq_item: dict, kb_context: dict | None = None) -> dict:
    """构造清单特征与标准清单/知识库的对比上下文，交给 AI 做业务推演。"""
    feature_text = boq_item.get("feature_text", "") or ""
    name = boq_item.get("name", "") or ""
    text = f"{name}\n{feature_text}"

    extracted = {
        "material_or_product": [],
        "specs": [],
        "work_content": [],
        "constraints": [],
    }

    for line in [x.strip() for x in text.splitlines() if x.strip()]:
        if "材质" in line or "材料" in line or "种类" in line:
            extracted["material_or_product"].append(line)
        elif "规格" in line or "尺寸" in line or "厚" in line or "C" in line or "DN" in line or "Φ" in line:
            extracted["specs"].append(line)
        elif "工作内容" in line or "安装" in line or "施工" in line:
            extracted["work_content"].append(line)
        elif "其他" in line or "详见" in line or "规范" in line or "标准" in line:
            extracted["constraints"].append(line)

    expected_materials = []
    expected_processes = []
    if kb_context:
        expected_materials = [r.get("material_name", "") for r in kb_context.get("material_rules", [])]
        expected_processes = [r.get("process_name", "") for r in kb_context.get("process_rules", []) if r.get("id") != "GENERIC-PROCESS"]

    standard_features = boq_item.get("standard_features", []) or []
    standard_work_items = boq_item.get("standard_work_items", []) or []
    standard_feature_compare = compare_standard_features(standard_features, feature_text)

    return {
        "input_code": boq_item.get("code", ""),
        "standard_code": boq_item.get("standard_code", ""),
        "standard_name": boq_item.get("standard_name", ""),
        "t1_section": boq_item.get("t1_section", ""),
        "component": boq_item.get("component", ""),
        "standard_work_scope": boq_item.get("work_scope", ""),
        "q0_match": boq_item.get("q0_match", {}),
        "standard_features": standard_features,
        "standard_work_items": standard_work_items,
        "standard_unit": boq_item.get("standard_unit", ""),
        "calculation_rule": boq_item.get("calculation_rule", ""),
        "standard_feature_comparison": standard_feature_compare,
        "actual_material_hints": extract_material_hints(text),
        "extracted_feature_points": extracted,
        "kb_expected_processes": expected_processes,
        "kb_expected_materials": expected_materials,
        "quota_candidates": (kb_context or {}).get("quota_candidates", []),
        "quota_materials": (kb_context or {}).get("quota_materials", []),
        "comparison_tasks": [
            "判断输入清单编码是否可归并到 standard_code 对应的标准分部分项。",
            "按 q0_match.trace 说明编码是完整命中还是从顺序码回退命中。",
            "逐项比较 actual_material_hints、standard_features、standard_work_items，指出实际清单比国标多写/少写了哪些特征。",
            "结合 quota_candidates 与 quota_materials 判断定额消耗材料对本条实际项目特征是否适用。",
            "判断项目特征是否改变标准清单的材料范围、工序范围或供应范围。",
            "判断知识库材料是否全部适用于本条清单；不适用的必须说明剔除原因。",
            "判断是否存在知识库未覆盖但项目特征明确要求的材料。",
        ],
    }


def build_L1_context(project_config: dict | None = None, cli_args=None) -> dict:
    """L1 项目级上下文：所有阶段可见，但只放稳定的项目级信息。

    优先级：project_config > CLI 参数 > 环境变量
    """
    project_config = project_config or {}
    # 从 CLI args 提取项目参数（若未在 project_config 中显式给出）
    _ = cli_args
    def _get(key: str, cli_val: str = "", env_key: str = "", fallback=""):
        if project_config.get(key):
            return project_config[key]
        if cli_val:
            return cli_val
        if env_key and os.getenv(env_key, ""):
            return os.getenv(env_key, "")
        return fallback

    return {
        "project_type": _get("type", "", "PROJECT_TYPE", ""),
        "project_category": _get(
            "project_category",
            _.project_category if _ else "",
            "PROJECT_CATEGORY",
            project_config.get("type", os.getenv("PROJECT_TYPE", "")),
        ),
        "structure_type": _get(
            "structure_type",
            _.structure_type if _ else "",
            "STRUCTURE_TYPE",
        ),
        "height_scope": _get(
            "height_scope",
            _.height_scope if _ else "",
            "HEIGHT_SCOPE",
        ),
        "region": _get(
            "region",
            _.project_region if _ else "",
            "PROJECT_REGION",
        ),
        "applicable_standards": project_config.get("standards", [
            "GB 50500 工程量清单计价规范体系",
            "GB 50204 混凝土结构工程施工质量验收规范",
        ]),
        "default_loss_rates": project_config.get("loss_rates", {}),
    }


def build_L2_context(boq_item: dict, kb_context: dict, feature_comparison: dict) -> dict:
    """L2 清单项级上下文：Meta 与 Stage1 的核心输入。"""
    feature_light = {
        k: v for k, v in feature_comparison.items()
        if k not in {"quota_candidates", "quota_materials", "comparison_tasks"}
    }
    return {
        "boq_item": {
            "code": boq_item.get("code", ""),
            "standard_code": boq_item.get("standard_code", ""),
            "standard_name": boq_item.get("standard_name", ""),
            "name": boq_item.get("name", ""),
            "feature_text": boq_item.get("feature_text", ""),
            "quantity": boq_item.get("quantity", 0),
            "unit": boq_item.get("unit", ""),
            "work_scope": boq_item.get("work_scope", ""),
            "standard_features": boq_item.get("standard_features", []),
            "standard_work_items": boq_item.get("standard_work_items", []),
            "code_name_conflict": boq_item.get("code_name_conflict"),
        },
        "q0_match": boq_item.get("q0_match", {}),
        "feature_comparison": feature_light,
        "quota_candidates": kb_context.get("quota_candidates", [])[:6],
        "quota_materials_summary": [
            {
                "material_name": r.get("material_name", ""),
                "spec": r.get("spec", ""),
                "unit": r.get("unit", ""),
                "quota_id": r.get("quota_id", ""),
            }
            for r in kb_context.get("quota_materials", [])[:12]
        ],
        "material_rules_summary": [
            {
                "id": r.get("id", ""),
                "material_name": r.get("material_name", ""),
                "required": r.get("required", ""),
                "basis": r.get("basis", ""),
            }
            for r in kb_context.get("material_rules", [])[:15]
        ],
        "project_defaults": {
            "query": kb_context.get("project_defaults", {}).get("query", {}),
            "matched_defaults": [
                {
                    "system_id": row.get("system_id", ""),
                    "system_name": row.get("system_name", ""),
                    "phase": row.get("phase", ""),
                    "match_level": row.get("match_level", ""),
                    "policy": row.get("policy", {}),
                    "recommendations": row.get("recommendations", [])[:6],
                }
                for row in kb_context.get("project_defaults", {}).get("matched_defaults", [])[:4]
            ],
            "prompt_injection": kb_context.get("project_defaults", {}).get("prompt_injection", ""),
            "risk_notes": kb_context.get("project_defaults", {}).get("risk_notes", []),
        },
    }


def build_L3_context(process: dict, boq_item: dict, prompt_plan: PromptPlan) -> dict:
    """L3 工序级上下文：Stage2 per-process 调用只看当前工序。"""
    return {
        "process": {
            "step": process.get("step"),
            "name": process.get("name", ""),
            "is_main": process.get("is_main", False),
            "description": process.get("description", ""),
            "typical_materials": process.get("typical_materials", ""),
        },
        "boq_item": {
            "standard_code": boq_item.get("standard_code", ""),
            "standard_name": boq_item.get("standard_name", ""),
            "name": boq_item.get("name", ""),
            "feature_text": boq_item.get("feature_text", ""),
            "unit": boq_item.get("unit", ""),
            "quantity": boq_item.get("quantity", 0),
            "standard_work_items": boq_item.get("standard_work_items", []),
        },
        "prompt_plan_focus": {
            "must_include_materials": prompt_plan.must_include_materials,
            "forbidden_materials": prompt_plan.forbidden_materials,
            "risk_flags": prompt_plan.risk_flags,
        },
    }


def build_L4_context(material: dict, kb_context: dict) -> dict:
    """L4 材料级上下文：Stage3/4/5 使用的材料校验视图。"""
    return {
        "material_name": material.get("material_name", ""),
        "raw_material_name": material.get("raw_material_name", ""),
        "classification": {
            "role": material.get("role", ""),
            "category_l1": material.get("category_l1", ""),
            "category_l2": material.get("category_l2", ""),
            "unit": material.get("unit", ""),
        },
        "loss_rule": {
            "waste_rate_estimate": material.get("waste_rate_estimate"),
            "loss_basis": material.get("loss_basis", ""),
        },
        "standardization": {
            "status": material.get("standardization_status", ""),
            "score": material.get("standardization_score", ""),
            "standard_refs": material.get("standard_refs", []),
            "standard_code": material.get("standard_code", ""),
        },
    }


def stage_system_prompt(prompt_plan: PromptPlan, stage: str, fallback: str) -> str:
    generated = getattr(prompt_plan, f"{stage}_prompt", "")
    if generated:
        return prompt_plan.wrap_stage_prompt(stage, generated)
    return fallback + prompt_plan.SAFETY_SHELL


def normalize_boq_item(raw: dict, fallback_args=None) -> dict:
    code = str(
        raw.get("code")
        or raw.get("清单编码")
        or raw.get("清单项目编码")
        or raw.get("项目编码")
        or raw.get("编码")
        or ""
    ).strip()
    name = str(
        raw.get("name")
        or raw.get("子目名称")
        or raw.get("清单名称")
        or raw.get("项目名称")
        or raw.get("名称")
        or ""
    ).strip()
    # 清理名称中误带入的特征文本（如 '"名称：xxx"'、'1.' 编号列表等）
    if name:
        name = re.sub(r'\s*"\s*名称[：:].*$', '', name)       # 未闭合引号的特征文本
        name = re.sub(r'\s*"\s*名称[：:][^"]*"\s*$', '', name)  # 闭合引号的特征文本
        # 真实客户表中设备编号可能写在名称第二行（如“风管机\nK-1”），不能一刀切删除。
        # 仅当换行后明显进入项目特征编号/“名称:”段落时才截断。
        name = re.split(r'\n\s*(?:\d+[\.、)]|名称[：:]|项目特征|工作内容)', name, maxsplit=1)[0]
        name = re.sub(r'[\r\n]+', ' ', name)
        name = re.sub(r'\s{2,}', ' ', name).strip()
    feature = str(
        raw.get("feature_text")
        or raw.get("feature")
        or raw.get("子目特征描述")
        or raw.get("项目特征描述")
        or raw.get("项目特征")
        or raw.get("特征描述")
        or raw.get("特征")
        or ""
    ).strip()
    qty_raw = raw.get("quantity") or raw.get("qty") or raw.get("工程量") or raw.get("工程数量") or raw.get("数量") or 0
    unit = str(raw.get("unit") or raw.get("计量单位") or raw.get("单位") or "").strip()
    t1 = str(raw.get("t1_section") or raw.get("t1") or raw.get("分部") or raw.get("专业") or "").strip()
    component = str(raw.get("component") or raw.get("构件") or raw.get("部位") or "").strip()

    if fallback_args:
        code = code or fallback_args.code
        name = name or fallback_args.name
        feature = feature or fallback_args.feature
        unit = unit or fallback_args.unit
        t1 = t1 or fallback_args.t1
        component = component or fallback_args.component
        if not qty_raw:
            qty_raw = fallback_args.qty

    try:
        quantity = float(str(qty_raw).replace(",", ""))
    except Exception:
        quantity = 0.0

    standard = infer_standard_boq_from_code(code, name, f"{feature}\n计量单位:{unit}")
    return {
        "code": code,
        "standard_code": standard.get("standard_code", code[:9]),
        "standard_name": standard.get("standard_name", name),
        "name": name,
        "feature_text": feature,
        "quantity": quantity,
        "unit": unit,
        "t1_section": t1 or standard.get("t1_section", ""),
        "component": component or standard.get("component", ""),
        "work_scope": standard.get("work_scope", ""),
        "standard_features": standard.get("standard_features", []),
        "standard_work_items": standard.get("standard_work_items", []),
        "standard_unit": standard.get("standard_unit", ""),
        "calculation_rule": standard.get("calculation_rule", ""),
        "q0_match": standard.get("q0_match", {}),
        "code_name_conflict": standard.get("code_name_conflict"),
        "concrete_strength": next(iter(re.findall(r"C\d+", feature.upper())), ""),
        "source_sheet": raw.get("source_sheet") or raw.get("来源工作表") or "",
        "source_row": raw.get("source_row") or raw.get("来源行号") or "",
        "source_section": raw.get("source_section") or raw.get("分组") or "",
        "sequence": raw.get("sequence") or raw.get("序号") or "",
    }


def parse_boq_text(text: str) -> list[dict]:
    """解析用户粘贴的清单文本。支持普通行、Excel复制的Tab行、带引号多行项目特征。"""
    text = (text or "").strip()
    if not text:
        return []

    items = []

    def clean_cell(value: str) -> str:
        value = str(value or "").strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        return value.replace('""', '"').strip()

    # Excel/表格复制优先：编码\t项目名称/特征\t...\t单位\t工程量
    code_positions = list(re.finditer(r"(?m)^\s*(?P<code>\d{9,12})\t", text))
    if code_positions:
        blocks = []
        for i, m in enumerate(code_positions):
            start = m.start()
            end = code_positions[i + 1].start() if i + 1 < len(code_positions) else len(text)
            blocks.append(text[start:end].strip())

        for block in blocks:
            parts = block.split("\t")
            code = clean_cell(parts[0]) if parts else ""
            unit = ""
            qty = 0
            qty_idx, unit_idx = -1, -1
            unit_values = {
                "m2", "m²", "㎡", "m3", "m³", "m", "t", "kg", "个", "项",
                "台", "套", "根", "块", "组", "张", "扇", "樘", "座", "条", "段",
            }
            for idx in range(len(parts) - 1, 0, -1):
                val = clean_cell(parts[idx])
                if not val:
                    continue
                if qty_idx < 0 and re.fullmatch(r"\d+(?:\.\d+)?", val):
                    qty = val
                    qty_idx = idx
                    continue
                if unit_idx < 0 and val in unit_values:
                    unit = val
                    unit_idx = idx

            text_parts = []
            for idx, part in enumerate(parts[1:], start=1):
                if idx in {qty_idx, unit_idx}:
                    continue
                val = clean_cell(part)
                if val:
                    text_parts.append(val)

            if not text_parts:
                continue
            name = text_parts[0]
            feature = "\n".join(text_parts[1:])
            name_lines = [x.strip() for x in name.splitlines() if x.strip()]
            if len(name_lines) > 1:
                name = name_lines[0]
                feature = "\n".join(name_lines[1:] + ([feature] if feature else []))

            # 「数量：1台」格式兜底提取
            if not qty or float(str(qty)) == 0:
                qty_fallback = re.search(
                    r'数量\s*[：:]\s*(\d+(?:\.\d+)?)\s*([^\s\n,，。；;]+)?',
                    block
                )
                if qty_fallback:
                    qty = qty_fallback.group(1)
                    if not unit and qty_fallback.group(2):
                        unit_candidate = qty_fallback.group(2).strip()
                        if len(unit_candidate) <= 3:
                            unit = unit_candidate

            items.append(normalize_boq_item({"code": code, "name": name, "feature_text": feature, "quantity": qty, "unit": unit}))

        return [x for x in items if x.get("code") or x.get("name")]

    # 简单一行一条：编码 名称 特征 工程量 单位
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        m = re.match(r"^(?P<code>\d{9,12})\s+(?P<rest>.+)$", line)
        if not m:
            continue
        code = m.group("code")
        rest = m.group("rest").strip().strip('"')
        qty_match = re.search(r"(?P<qty>(?<=\s)\d+(?:\.\d+)?)\s*(?P<unit>m³|m3|m²|m2|㎡|立方米|平方米|m|t|kg|个|项|台|套|根|块|组|张|扇|樘|座)?\s*$", rest)
        qty = 0
        unit = ""
        if qty_match:
            qty = qty_match.group("qty")
            unit = qty_match.group("unit") or ""
            rest = rest[:qty_match.start()].strip()

        name = rest
        feature = ""
        for token in [" C", " 厚", " 1:", " HRB", " Φ", " ф", " DN", " 规格"]:
            idx = rest.find(token)
            if idx > 0:
                name = rest[:idx].strip()
                feature = rest[idx:].strip()
                break

        items.append(normalize_boq_item({"code": code, "name": name, "feature_text": feature, "quantity": qty, "unit": unit}))

    if items:
        return items
    return [normalize_boq_item({"name": text[:80], "feature_text": text})]


def parse_txt_boq_items(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return parse_boq_text(f.read())


def clean_excel_cell(value) -> str:
    """Excel 单元格统一转文本，保留换行项目特征。"""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def normalize_excel_header(value) -> str:
    text = clean_excel_cell(value)
    text = re.sub(r"\s+", "", text)
    text = text.replace("：", ":").replace("（", "(").replace("）", ")")
    return text


def canonical_excel_header(value) -> str:
    """把客户清单里不同叫法的表头归一到 normalize_boq_item 能识别的字段。"""
    header = normalize_excel_header(value)
    header_map = {
        "序号": "序号",
        "清单编码": "清单编码",
        "项目编码": "清单编码",
        "清单项目编码": "清单编码",
        "编码": "清单编码",
        "子目名称": "子目名称",
        "项目名称": "子目名称",
        "清单名称": "子目名称",
        "名称": "子目名称",
        "子目特征描述": "子目特征描述",
        "项目特征描述": "子目特征描述",
        "项目特征": "子目特征描述",
        "特征描述": "子目特征描述",
        "特征": "子目特征描述",
        "计量单位": "计量单位",
        "单位": "计量单位",
        "工程量": "工程量",
        "工程数量": "工程量",
        "数量": "工程量",
    }
    if header in header_map:
        return header_map[header]
    if "编码" in header and len(header) <= 8:
        return "清单编码"
    if "名称" in header and "单价" not in header and "合价" not in header:
        return "子目名称"
    if "特征" in header or "描述" in header:
        return "子目特征描述"
    if "单位" in header:
        return "计量单位"
    if header in {"工程量", "数量"}:
        return "工程量"
    return header


def score_excel_header_row(values: tuple) -> int:
    headers = [canonical_excel_header(v) for v in values]
    unique_headers = set(h for h in headers if h)
    score = 0
    for required in ["清单编码", "子目名称", "子目特征描述", "计量单位", "工程量"]:
        if required in unique_headers:
            score += 3
    if "序号" in unique_headers:
        score += 1
    # 真实表头通常不会出现长段落项目特征。
    if any(len(clean_excel_cell(v)) > 30 for v in values):
        score -= 2
    return score


def find_excel_boq_header_row(ws) -> tuple[int | None, list[str]]:
    """自动定位 BOQ 表头行。客户表常见情况：前几行是标题/工程名称，第3行才是表头。"""
    best_row = None
    best_headers = []
    best_score = 0
    scan_limit = min(ws.max_row or 0, 40)
    for row_idx in range(1, scan_limit + 1):
        values = tuple(cell.value for cell in ws[row_idx])
        score = score_excel_header_row(values)
        if score > best_score:
            best_score = score
            best_row = row_idx
            best_headers = [canonical_excel_header(v) for v in values]
    if best_score < 9:
        return None, []
    return best_row, best_headers


def looks_like_boq_code(value: str) -> bool:
    return bool(re.fullmatch(r"\d{9,12}", clean_excel_cell(value)))


def parse_excel_quantity(value) -> float | str:
    if value in [None, ""]:
        return ""
    if isinstance(value, (int, float)):
        return float(value)
    text = clean_excel_cell(value).replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return m.group(0) if m else ""


def is_excel_section_or_total_row(record: dict) -> bool:
    code = clean_excel_cell(record.get("清单编码"))
    name = clean_excel_cell(record.get("子目名称"))
    feature = clean_excel_cell(record.get("子目特征描述"))
    unit = clean_excel_cell(record.get("计量单位"))
    qty = clean_excel_cell(record.get("工程量"))
    joined = f"{code} {name} {feature}".strip()
    if not joined:
        return True
    if any(word in joined for word in ["小计", "合计", "汇总", "总计"]):
        return True
    if not looks_like_boq_code(code):
        # 分部标题行通常只有名称，没有单位/工程量/特征。
        return not (name and (feature or unit or qty))
    return False


def parse_xlsx_boq_items(path: str) -> list[dict]:
    """读取真实客户 Excel 清单：自动选工作表、找表头、跳过分部/小计行。"""
    try:
        from openpyxl import load_workbook
    except ImportError as e:
        raise ImportError("读取 Excel 需要安装 openpyxl：pip install openpyxl") from e

    wb = load_workbook(path, data_only=True)
    items: list[dict] = []

    for ws in wb.worksheets:
        header_row, headers = find_excel_boq_header_row(ws)
        if not header_row:
            continue

        current_section = ""
        for row_idx in range(header_row + 1, (ws.max_row or header_row) + 1):
            values = [cell.value for cell in ws[row_idx]]
            raw_record = {}
            for col_idx, header in enumerate(headers):
                if not header or col_idx >= len(values):
                    continue
                value = values[col_idx]
                if value not in [None, ""]:
                    raw_record[header] = value

            if not raw_record:
                continue

            code = clean_excel_cell(raw_record.get("清单编码"))
            name = clean_excel_cell(raw_record.get("子目名称"))
            feature = clean_excel_cell(raw_record.get("子目特征描述"))
            unit = clean_excel_cell(raw_record.get("计量单位"))
            qty = parse_excel_quantity(raw_record.get("工程量"))

            # 记录当前分组标题，便于后续审计；不把标题当清单项。
            if not looks_like_boq_code(code) and name and not feature and not unit and not qty:
                current_section = name
                continue

            record = {
                "清单编码": code,
                "子目名称": name,
                "子目特征描述": feature,
                "计量单位": unit,
                "工程量": qty,
                "序号": raw_record.get("序号", ""),
                "source_sheet": ws.title,
                "source_row": row_idx,
                "source_section": current_section,
            }
            if is_excel_section_or_total_row(record):
                continue

            item = normalize_boq_item(record)
            if item.get("code") or item.get("name"):
                items.append(item)

    return items


def collect_project_info() -> dict:
    """交互式采集项目级信息，用于默认值库精准匹配。"""
    print("\n—— 项目信息（可选，回车跳过） ——")
    info = {}
    info["project_category"] = input("项目类别 [居住建筑/办公建筑/商业建筑/工业建筑]: ").strip()
    info["structure_type"] = input("结构类型 [框架结构/剪力墙结构/钢结构/框架剪力墙结构]: ").strip()
    info["height_scope"] = input("高度范围 [多层/高层/超高层]: ").strip()
    info["project_region"] = input("项目地区 [华北/华东/华南/西南/西北]: ").strip()
    info["t1_section"] = input("分部工程 (如 混凝土及钢筋混凝土工程): ").strip()
    info["component"] = input("构件/部位 (如 框架柱、外墙): ").strip()
    return {k: v for k, v in info.items() if v}


def read_interactive_boq_items(project_info: dict | None = None) -> tuple[list[dict], dict]:
    """交互式清单输入。支持文件路径或直接粘贴文本，并采集项目信息。
    返回 (items, project_info)。"""
    project_info = project_info or {}

    print("\n" + "=" * 50)
    print("请选择清单输入方式：")
    print("  1. 输入文件路径（支持 .xlsx / .csv / .json / .txt）")
    print("  2. 直接粘贴清单文本（简洁格式）")
    choice = input("输入数字 [1-2]（默认 2）: ").strip()

    if choice == "1":
        filepath = input("文件路径: ").strip()
        filepath = os.path.expanduser(filepath)
        if not filepath:
            raise ValueError("未输入文件路径。")
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"文件不存在：{filepath}")

        if not project_info:
            project_info = collect_project_info()

        # 创建临时 args 对象驱动 load_boq_items_from_input
        class _FakeArgs:
            input = filepath
            sample = False
            interactive = False
            limit = 0
        items = load_boq_items_from_input(_FakeArgs())

        # 将项目信息作为默认值注入每个 item
        for item in items:
            for key in ["t1_section", "component"]:
                if not item.get(key) and project_info.get(key):
                    item[key] = project_info[key]

        print(f"已加载 {len(items)} 条清单项")
        return items, project_info

    # 直接粘贴文本模式
    if not project_info:
        project_info = collect_project_info()

    print("\n请输入清单内容。支持一行一条，格式：编码 名称 特征参数... 数量 单位")
    print("示例: 010502001 矩形柱（现浇混凝土） C30 48.6 m³")
    print("      011201001 墙面一般抹灰 1:3水泥砂浆 厚20mm 320 m²")
    print("输入 END 后开始拆解。\n")

    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().upper() == "END":
            break
        lines.append(line)

    text = "\n".join(lines).strip()
    if not text:
        raise ValueError("未输入清单内容。")

    items = parse_boq_text(text)

    # 注入项目信息
    for item in items:
        for key in ["t1_section", "component"]:
            if not item.get(key) and project_info.get(key):
                item[key] = project_info[key]

    return items, project_info


# 交互模式下采集的项目信息，供后续 L1 上下文使用
_INTERACTIVE_PROJECT_INFO: dict = {}


def load_boq_items_from_input(args) -> list[dict]:
    global _INTERACTIVE_PROJECT_INFO
    if not args.input:
        # 直接运行脚本时，默认进入交互输入；需要跑样例时显式传 --sample。
        if args.sample:
            return [normalize_boq_item({}, fallback_args=args)]
        if args.interactive or len(sys.argv) == 1:
            items, proj_info = read_interactive_boq_items()
            _INTERACTIVE_PROJECT_INFO = proj_info
            return items
        return [normalize_boq_item({}, fallback_args=args)]

    path = os.path.abspath(args.input)
    if not os.path.exists(path):
        raise FileNotFoundError(f"清单输入文件不存在：{path}")

    ext = os.path.splitext(path)[1].lower()
    if ext == ".txt":
        items = parse_txt_boq_items(path)
    elif ext == ".json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("items") or data.get("清单") or [data]
        items = [normalize_boq_item(x) for x in data]
    elif ext == ".csv":
        import csv
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        items = [normalize_boq_item(row) for row in rows]
    elif ext in [".xlsx", ".xlsm"]:
        items = parse_xlsx_boq_items(path)
    else:
        raise ValueError(f"不支持的输入文件类型：{ext}，请使用 .txt/.json/.csv/.xlsx")

    if args.limit and args.limit > 0:
        items = items[:args.limit]
    return [x for x in items if x.get("name") or x.get("code")]


def load_json_file(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_kb_dir(args) -> str:
    if args.kb_dir:
        return os.path.abspath(args.kb_dir)
    candidate = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb")
    return candidate if os.path.isdir(candidate) else ""


# ----------- 人工确认知识库：用于沉淀正确拆解经验 -----------
def resolve_approved_kb_path(args) -> str:
    if args.approved_kb:
        return os.path.abspath(args.approved_kb)
    base_dir = resolve_kb_dir(args) or os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb")
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, "已确认材料规则.json")


def load_approved_kb(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("rules", [])
    if isinstance(data, list):
        return data
    return []


def save_approved_kb(path: str, rules: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "version": "1.0",
        "description": "人工确认后的清单材料拆解知识库。该库用于沉淀用户认可的正确材料拆解结果，并在后续同类清单中作为优先上下文。",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "rules": rules,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def feature_signature(boq_item: dict) -> str:
    base = "|".join(
        [
            str(boq_item.get("standard_code", "")),
            str(boq_item.get("name", "")),
            str(boq_item.get("feature_text", "")),
            str(boq_item.get("unit", "")),
        ]
    )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def find_approved_rules(boq_item: dict, approved_rules: list[dict]) -> list[dict]:
    code = str(boq_item.get("code", ""))
    standard_code = str(boq_item.get("standard_code", ""))
    name = str(boq_item.get("name", ""))
    sig = feature_signature(boq_item)

    matched = []
    for rule in approved_rules:
        if rule.get("feature_signature") == sig:
            matched.append(rule)
            continue
        if standard_code and rule.get("standard_code") == standard_code:
            matched.append(rule)
            continue
        if code and rule.get("code") == code:
            matched.append(rule)
            continue
        if name and rule.get("name") == name:
            matched.append(rule)
    return matched[:5]


def parse_material_names(text: str) -> set[str]:
    text = (text or "").strip()
    if not text:
        return set()
    parts = re.split(r"[,，、;；\n]+", text)
    return {p.strip() for p in parts if p.strip()}


def collect_learning_feedback(boq_item: dict, result: dict, approved_kb: list[dict]) -> dict | None:
    print("\n=== 人工确认与知识写回 ===")
    print("当前材料结果：")
    for idx, m in enumerate(result.get("materials", []), start=1):
        print(f"  {idx}. {m.get('material_name')} | {m.get('role', '')} | {m.get('category_l1', '')}/{m.get('category_l2', '')} | 损耗{m.get('waste_rate_estimate')}")

    answer = input("是否确认本次拆解结果可沉淀为正确知识？输入 y 确认，其他跳过：").strip().lower()
    if answer != "y":
        print("已跳过知识写回。")
        return None

    rejected_input = input("请输入需要排除/不认可的材料名称，多个用逗号分隔；没有则回车：").strip()
    add_input = input("请输入需要补充认可的材料名称，多个用逗号分隔；没有则回车：").strip()
    note = input("请输入本次确认说明/适用条件；没有则回车：").strip()

    rejected = parse_material_names(rejected_input)
    additions = parse_material_names(add_input)

    approved_materials = []
    for m in result.get("materials", []):
        material_name = m.get("material_name", "")
        if not material_name or material_name in rejected:
            continue
        approved_materials.append(
            {
                "material_name": material_name,
                "spec_hint": m.get("spec_hint", ""),
                "role": m.get("role", ""),
                "category_l1": m.get("category_l1", ""),
                "category_l2": m.get("category_l2", ""),
                "unit": m.get("unit", ""),
                "waste_rate_estimate": m.get("waste_rate_estimate"),
                "loss_basis": m.get("loss_basis", ""),
                "coefficient": m.get("coefficient"),
                "source": m.get("source", ""),
                "reason": m.get("reason", ""),
            }
        )

    for name in additions:
        if name not in {x["material_name"] for x in approved_materials}:
            approved_materials.append(
                {
                    "material_name": name,
                    "spec_hint": "人工补充",
                    "role": "待分类",
                    "category_l1": "",
                    "category_l2": "",
                    "unit": "",
                    "waste_rate_estimate": None,
                    "loss_basis": "人工补充，待损耗库确认。",
                    "coefficient": None,
                    "source": "人工确认补充",
                    "reason": "用户确认需要补充该材料。",
                }
            )

    rule = {
        "id": f"APPROVED-{boq_item.get('standard_code', boq_item.get('code', 'UNKNOWN'))}-{feature_signature(boq_item)}-{int(time.time())}",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "code": boq_item.get("code", ""),
        "standard_code": boq_item.get("standard_code", ""),
        "standard_name": boq_item.get("standard_name", ""),
        "name": boq_item.get("name", ""),
        "feature_text": boq_item.get("feature_text", ""),
        "feature_signature": feature_signature(boq_item),
        "unit": boq_item.get("unit", ""),
        "t1_section": boq_item.get("t1_section", ""),
        "component": boq_item.get("component", ""),
        "approved_materials": approved_materials,
        "rejected_materials": sorted(rejected),
        "user_note": note,
        "processes": result.get("processes", []),
        "validation_issues": result.get("validation_issues", []),
    }

    approved_kb.append(rule)
    print(f"已生成确认规则：{rule['id']}")
    return rule
api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("ARK_API_KEY") or os.getenv("MOONSHOT_API_KEY", "")
model = os.getenv("DEEPSEEK_MODEL") or os.getenv("ARK_MODEL") or os.getenv("MOONSHOT_MODEL", "deepseek-v4-pro")
base_url = os.getenv("DEEPSEEK_BASE_URL") or os.getenv("ARK_BASE_URL") or "https://api.moonshot.cn/v1"
if os.getenv("DEEPSEEK_API_KEY"):
    provider = "deepseek"
elif os.getenv("ARK_API_KEY"):
    provider = "ark"
else:
    provider = "moonshot"

def mask_key(key: str) -> str:
    if not key:
        return "EMPTY"
    if len(key) <= 10:
        return "***"
    return key[:6] + "..." + key[-4:]

print(f"API Key: {mask_key(api_key)} | Model: {model}")

from openai import OpenAI
import httpx
http_client = httpx.Client(
    proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or None,
    timeout=httpx.Timeout(connect=20.0, read=180.0, write=30.0, pool=20.0),
)
client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)

RUN_REVIEW = os.getenv("RUN_REVIEW", "0") == "1"
RUN_AI_MATERIALS = os.getenv("RUN_AI_MATERIALS", "1") == "1"
RUN_META_STRATEGY = os.getenv("RUN_META_STRATEGY", "1") == "1"
STAGE2_MAX_CONCURRENCY = max(1, int(os.getenv("STAGE2_MAX_CONCURRENCY", "2")))
AI_MAX_RETRIES = max(1, int(os.getenv("AI_MAX_RETRIES", "3")))
AI_RETRY_BASE_DELAY = max(0.1, float(os.getenv("AI_RETRY_BASE_DELAY", "1.2")))


def is_placeholder_payload(obj, key: str) -> bool:
    """过滤 prompt 示例 JSON，避免把“材料名称/工序名称”当成真实结果。"""
    if not isinstance(obj, dict):
        return False
    rows = obj.get(key)
    if not isinstance(rows, list) or not rows:
        return False
    placeholders = {"材料名称", "工序名称", "工序说明", "材料线索", "标准清单编码", "标准清单名称"}
    hit = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        values = {str(v).strip() for v in row.values() if isinstance(v, str)}
        if values & placeholders:
            hit += 1
    return hit > 0


def extract_json(text: str):
    """从模型返回文本中提取 JSON。优先选择包含 processes/materials 的最终对象，避免误抽 prompt 中的示例 JSON。"""
    if text is None:
        raise ValueError("AI 返回为空：None")
    text = text.strip()
    if not text:
        raise ValueError("AI 返回为空字符串")

    text = re.sub(r"^```(?:json|JSON)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # 尝试修复末尾截断的 JSON
    def _try_fix_truncated(s: str) -> str:
        if not s.startswith("{"):
            return s
        open_b = s.count("{") - s.count("}")
        open_sq = s.count("[") - s.count("]")
        if open_b > 0 or open_sq > 0:
            if s[-1] not in ('"', '}', ']'):
                s += '"'
            s += "]" * open_sq + "}" * open_b
        return s

    try:
        obj = json.loads(text)
        if isinstance(obj, (dict, list)):
            return obj
    except json.JSONDecodeError:
        pass

    # 尝试修复截断后重新加载
    fixed = _try_fix_truncated(text)
    if fixed != text:
        try:
            obj = json.loads(fixed)
            if isinstance(obj, (dict, list)):
                return obj
        except json.JSONDecodeError:
            pass

    candidates = []
    for start in [i for i, ch in enumerate(text) if ch in "{["]:
        opener = text[start]
        closer = "}" if opener == "{" else "]"
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    snippet = text[start:idx + 1]
                    try:
                        parsed = json.loads(snippet)
                        candidates.append(parsed)
                    except json.JSONDecodeError:
                        pass
                    break

    if not candidates:
        raise ValueError(f"无法解析 JSON，AI 原始返回：\n{text[:1000]}")

    for key in ["materials", "processes"]:
        keyed = [c for c in candidates if isinstance(c, dict) and key in c]
        if keyed:
            meaningful = [c for c in keyed if not is_placeholder_payload(c, key)]
            return (meaningful or keyed)[-1]

    dicts = [c for c in candidates if isinstance(c, dict)]
    if dicts:
        return dicts[-1]

    lists = [c for c in candidates if isinstance(c, list)]
    if lists:
        return lists[-1]

    return candidates[-1]


def ensure_object_with_key(value, key: str) -> dict:
    """确保模型解析结果是 dict；如果返回 list，则包装成 {key: list}。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {key: value}
    raise ValueError(f"解析结果类型不支持：{type(value).__name__}，期望 dict 或 list")


def normalize_process_schema(value) -> dict:
    """兼容模型把工序写成 work_processes/steps/施工工序 等字段。"""
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        rows = []
        for key in ["processes", "work_processes", "construction_processes", "process_list", "steps", "施工工序", "工序列表", "工序"]:
            if isinstance(value.get(key), list):
                rows = value.get(key) or []
                break
        if not rows:
            for nested in value.values():
                if isinstance(nested, dict):
                    nested_rows = normalize_process_schema(nested).get("processes", [])
                    if nested_rows:
                        rows = nested_rows
                        break
                elif isinstance(nested, list):
                    dict_rows = [x for x in nested if isinstance(x, dict)]
                    if dict_rows and any(any(k in x for k in ["name", "process_name", "工序名称", "步骤名称"]) for x in dict_rows):
                        rows = nested
                        break
    else:
        rows = []

    normalized = []
    for idx, row in enumerate(rows):
        if isinstance(row, str):
            name = row.strip()
            item = {"step": idx + 1, "name": name, "description": name, "typical_materials": "", "is_main": True}
        elif isinstance(row, dict):
            name = (
                row.get("name")
                or row.get("process_name")
                or row.get("工序名称")
                or row.get("步骤名称")
                or row.get("step_name")
                or ""
            )
            description = (
                row.get("description")
                or row.get("desc")
                or row.get("工序说明")
                or row.get("说明")
                or row.get("work_content")
                or ""
            )
            typical_materials = (
                row.get("typical_materials")
                or row.get("materials_hint")
                or row.get("材料线索")
                or row.get("materials")
                or ""
            )
            if isinstance(typical_materials, list):
                typical_materials = "、".join(str(x) for x in typical_materials if str(x).strip())
            item = dict(row)
            item["step"] = row.get("step") or row.get("sequence") or row.get("序号") or idx + 1
            item["name"] = str(name).strip()
            item["description"] = str(description or name).strip()
            item["typical_materials"] = str(typical_materials).strip()
            item["is_main"] = bool(row.get("is_main", True))
        else:
            continue

        name = item.get("name", "")
        # 避免 JSON 片段被误当工序。
        if not name or '":' in name or name.startswith(("{", "[")) or name in {"standard_match", "comparison_summary", "evidence"}:
            continue
        normalized.append(item)
    return {"processes": normalized}


def normalize_material_schema(value) -> dict:
    """兼容模型把 materials 写成 material_candidates/candidate_materials 等字段。"""
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        rows = []
        for key in ["materials", "material_candidates", "candidate_materials", "materials_list", "候选材料", "材料清单"]:
            if isinstance(value.get(key), list):
                rows = value.get(key) or []
                break
        if not rows:
            for nested in value.values():
                if isinstance(nested, dict):
                    nested_rows = normalize_material_schema(nested).get("materials", [])
                    if nested_rows:
                        rows = nested_rows
                        break
    else:
        rows = []

    normalized = []
    for row in rows:
        if isinstance(row, str):
            normalized.append({"material_name": row})
            continue
        if not isinstance(row, dict):
            continue
        item = dict(row)
        if not item.get("material_name"):
            for key in ["name", "material", "candidate", "材料名称", "候选材料名称"]:
                if item.get(key):
                    item["material_name"] = item.get(key)
                    break
        normalized.append(item)
    return {"materials": normalized}


def get_message_text(message) -> str:
    """优先读取最终回答；reasoning_content 只用于诊断，不作为 JSON 解析输入。"""
    content = getattr(message, "content", None) or ""
    reasoning = getattr(message, "reasoning_content", None) or ""
    if content.strip():
        return content.strip()
    if reasoning.strip():
        return reasoning.strip()
    return ""

def build_processes_from_text(raw: str) -> dict:
    """当模型输出推理文本但没有JSON时，从文本中做通用兜底抽取，不写死专业工序。"""
    raw = str(raw or "")
    try:
        parsed_processes = normalize_process_schema(extract_json(raw))
        if parsed_processes.get("processes"):
            return parsed_processes
    except Exception:
        pass
    candidates = []
    for line in split_numbered_items(raw):
        line = re.sub(r"^(工序名称|工序|步骤|name)[:：]", "", line).strip()
        if not line:
            continue
        if '":' in line or line.startswith(("{", "[")):
            continue
        if any(x in line for x in ["施工", "安装", "铺贴", "涂刷", "绑扎", "浇筑", "养护", "拆除", "制作", "支设", "固定", "处理", "验收"]):
            candidates.append(line[:40])
    ordered = list(dict.fromkeys(candidates))[:8] or ["清单特征识别"]
    return {
        "processes": [
            {
                "step": idx + 1,
                "name": name,
                "description": "从模型文本解析得到的工序候选，需后续材料与规范门禁校验。",
                "typical_materials": "",
                "is_main": True,
            }
            for idx, name in enumerate(ordered)
        ]
    }

def build_materials_from_text_or_kb(raw: str, kb_context: dict) -> dict:
    """当模型输出推理文本但没有 JSON 时，从模型文本、知识库材料、工序材料线索中抽取候选材料，避免流程中断。"""
    raw = raw or ""
    candidates = []
    seen = set()

    def add_material(name: str, reason: str, trigger_process: str = "模型文本抽取", confidence: str = "medium"):
        name = str(name or "").strip()
        if not name or name in seen:
            return
        boq_item = kb_context.get("boq_item", {}) or {}
        if is_integrated_hvac_equipment(boq_item) and ("风机" in compact_text(name) or compact_text(name).endswith("安装")):
            return
        candidates.append(
            {
                "material_name": name,
                "spec_hint": "",
                "reason": reason[:60],
                "trigger_process": trigger_process,
                "evidence": "模型推理文本或知识库上下文命中",
                "confidence": confidence,
                "applicability": "适用",
            }
        )
        seen.add(name)

    # 1. 优先从 material_rules 命中材料和别名。
    for rule in kb_context.get("material_rules", []):
        names = [rule.get("material_name", "")] + rule.get("aliases", [])
        if any(name and name in raw for name in names):
            add_material(rule.get("material_name", ""), rule.get("basis", "模型文本命中知识库材料"), "知识库材料映射", "medium")

    # 2. 从工序规则的 materials_hint 中抽取。
    for rule in kb_context.get("process_rules", []):
        process_name = rule.get("process_name", "模型文本抽取")
        for name in rule.get("materials_hint", []):
            if name and name in raw:
                add_material(name, "模型文本命中工序材料线索", process_name, "medium")

    # 2.5 从 Q1/Q2 定额材料消耗中抽取。只有文本中也出现时才自动加入，避免套错相近定额。
    for row in kb_context.get("quota_materials", []):
        name = row.get("material_name", "")
        if name and name in raw:
            add_material(name, "模型文本命中定额消耗材料", "Q2定额材料消耗", "medium")

    # 3. 从 T3 标准物料动态词库抽取，不再维护 Python 硬编码材料清单。
    for name in extract_materials_by_lexicon(raw):
        add_material(name, "模型推理文本命中T3标准物料词库", "T3动态词库抽取", "medium")

    # 4. material_rules 有 required=True 但模型文本未抽到时，按知识库兜底。
    if not candidates:
        for rule in kb_context.get("material_rules", []):
            if rule.get("required") is True:
                add_material(rule.get("material_name", ""), rule.get("basis", "知识库required材料"), "知识库required兜底", "medium")

    return {"materials": candidates}

def build_processes_from_text_or_context(raw: str, boq_item: dict, kb_context: dict) -> dict:
    """模型没有返回标准 JSON 时，按当前清单上下文兜底，避免错误兜底成混凝土柱工序。"""
    raw = raw or ""
    kb_rules = [
        r for r in kb_context.get("process_rules", [])
        if r.get("id") != "GENERIC-PROCESS" and not str(r.get("id", "")).startswith("Q0-")
    ]
    q0_rules = [r for r in kb_context.get("process_rules", []) if str(r.get("id", "")).startswith("Q0-")]
    if kb_rules:
        return build_processes_from_kb(kb_context)
    default_processes = build_processes_from_project_defaults(kb_context)
    if default_processes.get("processes"):
        return default_processes
    if raw.strip():
        return build_processes_from_text(raw)
    if q0_rules:
        return build_processes_from_kb(kb_context)
    return {
        "processes": [
            {
                "step": 1,
                "name": "清单特征识别",
                "description": "知识库未命中，且模型无可解析工序；仅保留低置信占位工序并交由准确性门禁复核。",
                "typical_materials": "",
                "is_main": True,
            }
        ]
    }

def build_processes_from_project_defaults(kb_context: dict) -> dict:
    """默认推荐库命中做法体系时，用体系内 process_steps 生成工序链。"""
    rows = (kb_context.get("project_defaults") or {}).get("matched_defaults", []) or []
    candidates = []
    for row in sorted(rows, key=lambda x: -int(x.get("priority", 0))):
        for step in row.get("process_steps", []) or []:
            name = step.get("name") if isinstance(step, dict) else str(step)
            if not name:
                continue
            candidates.append({
                "step": len(candidates) + 1,
                "name": name,
                "description": (
                    step.get("description")
                    if isinstance(step, dict)
                    else f"项目默认推荐库命中 {row.get('system_name', '')}"
                ) or f"项目默认推荐库命中 {row.get('system_name', '')}",
                "typical_materials": "、".join(step.get("typical_materials", []) or [])
                if isinstance(step, dict)
                else "",
                "is_main": True,
                "evidence": row.get("match_level", ""),
            })
    return {"processes": candidates[:8]}

# 新增: 从知识库 process_rules 直接生成工序链（知识库强命中时）
def build_processes_from_kb(kb_context: dict) -> dict:
    """知识库已命中专用工序规则时，直接生成工序链，避免调用大模型。"""
    rules = [r for r in kb_context.get("process_rules", []) if r.get("id") != "GENERIC-PROCESS"]
    dedicated_rules = [r for r in rules if not str(r.get("id", "")).startswith("Q0-")]
    if dedicated_rules:
        rules = dedicated_rules
    rules = sorted(rules, key=lambda x: x.get("order", 999))
    return {
        "processes": [
            {
                "step": idx + 1,
                "name": r.get("process_name", ""),
                "description": r.get("constraint", ""),
                "typical_materials": "、".join(r.get("materials_hint", [])),
                "is_main": bool(r.get("required")),
            }
            for idx, r in enumerate(rules)
        ]
    }

def call_ai(step_name: str, messages: list, max_tokens: int):
    start = time.time()
    prompt_len = sum(len(m.get("content", "")) for m in messages)
    print(f"[{step_name}] 开始 | prompt={prompt_len}字 | max_tokens={max_tokens}")
    try:
        kwargs = dict(model=model, messages=messages, max_tokens=max_tokens, temperature=1, stream=False)
        if provider == "deepseek":
            kwargs["reasoning_effort"] = "high"
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        response = client.chat.completions.create(**kwargs)
    except Exception as e:
        elapsed = time.time() - start
        raise RuntimeError(f"[{step_name}] AI 调用失败 | 耗时={elapsed:.2f}s | {type(e).__name__}: {e}") from e

    elapsed = time.time() - start
    message = response.choices[0].message
    raw = get_message_text(message)
    print(f"[{step_name}] 返回 | 耗时={elapsed:.2f}s | raw={len(raw)}字")
    if not raw:
        raise RuntimeError(f"[{step_name}] AI 返回为空")
    return raw


# ── 异步 AI 客户端与 Stage2 per-process 并发 ──

_async_client = None

def get_async_client():
    global _async_client
    if _async_client is None:
        from openai import AsyncOpenAI
        import httpx as _httpx
        _async_http = _httpx.AsyncClient(proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or None, timeout=_httpx.Timeout(connect=20.0, read=180.0, write=30.0, pool=20.0))
        _async_client = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=_async_http)
    return _async_client


async def call_ai_async(step_name: str, messages: list, max_tokens: int) -> str:
    """异步 AI 调用 — 用于 Stage2 per-process 并发。"""
    import time as _time
    start = _time.time()
    prompt_len = sum(len(m.get("content", "")) for m in messages)
    print(f"[{step_name}] 开始(异步) | prompt={prompt_len}字 | max_tokens={max_tokens}")
    async_client = get_async_client()
    last_error = None
    for attempt in range(1, AI_MAX_RETRIES + 1):
        try:
            kwargs = dict(model=model, messages=messages, max_tokens=max_tokens, temperature=1, stream=False)
            if provider == "deepseek":
                kwargs["reasoning_effort"] = "high"
                kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            response = await async_client.chat.completions.create(**kwargs)
            break
        except Exception as e:
            last_error = e
            error_text = f"{type(e).__name__}: {e}"
            retryable = (
                "RateLimit" in type(e).__name__
                or "rate_limit" in error_text.lower()
                or "concurrency" in error_text.lower()
                or "429" in error_text
                or "APIConnectionError" in type(e).__name__
            )
            if attempt >= AI_MAX_RETRIES or not retryable:
                elapsed = _time.time() - start
                raise RuntimeError(f"[{step_name}] AI 异步调用失败 | 耗时={elapsed:.2f}s | {error_text}") from e
            delay = AI_RETRY_BASE_DELAY * attempt
            print(f"[{step_name}] 限流/连接异常，{delay:.1f}s 后重试 {attempt}/{AI_MAX_RETRIES}: {error_text}")
            await asyncio.sleep(delay)
    else:
        elapsed = _time.time() - start
        raise RuntimeError(f"[{step_name}] AI 异步调用失败 | 耗时={elapsed:.2f}s | {last_error}") from last_error

    elapsed = _time.time() - start
    message = response.choices[0].message
    raw = get_message_text(message)
    print(f"[{step_name}] 返回(异步) | 耗时={elapsed:.2f}s | raw={len(raw)}字")
    if not raw:
        raise RuntimeError(f"[{step_name}] AI 异步返回为空")
    return raw


async def derive_materials_for_process(
    process: dict,
    boq_item: dict,
    feature_comparison: dict,
    strategy: PromptPlan,
    L1: dict,
    L2: dict,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Stage2: 为单个工序推导候选材料。异步调用 AI，由 semaphore 控制并发上限。"""
    async with semaphore:
        process_name = process.get("name", "未知工序")
        try:
            L3 = build_L3_context(process, boq_item, strategy)
            raw = await call_ai_async(
                f"Stage2 per-process: {process_name}",
                [
                    {
                        "role": "system",
                        "content": stage_system_prompt(
                            strategy,
                            "stage2",
                            "你是建筑工程材料拆解专家。只输出JSON对象，不要输出分析过程。",
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"L1项目上下文:{json.dumps(L1, ensure_ascii=False, separators=(',', ':'))}\\n"
                            f"L2清单上下文:{json.dumps(L2, ensure_ascii=False, separators=(',', ':'))}\\n"
                            f"L3当前工序上下文:{json.dumps(L3, ensure_ascii=False, separators=(',', ':'))}\\n"
                            f"项目默认推荐提示:{L2.get('project_defaults', {}).get('prompt_injection', '')}\\n"
                            "\\n"
                            "请为该工序推导所需的采购材料。每个材料包含: material_name, spec_hint, reason, confidence, source。\\n"
                            "若采纳项目默认推荐库的候选材料，必须额外输出 source_type=project_default_kb，且 confidence 不得高于 medium；默认推荐不得覆盖项目特征明示材料。\\n"
                            "关键约束：若输入项目特征明示了具体材料材质/类型（如「钢制保温门」），则材料名称必须保留该材质/类型，"
                            "不得替换为 T3 库中或默认推荐库中材质不同的近似材料（如不得替换为铝合金门、冷藏库门、塑钢门）。"
                            "若 T3 库无对应条目，保留原始名称并标记 confidence=low。\\n"
                            "不要输出JSON之外的任何文字。\\n"
                            f"最终返回JSON格式：{MATERIAL_OUTPUT_EXAMPLE}"
                        ),
                    },
                ],
                max_tokens=1600,
            )
            try:
                parsed = normalize_material_schema(extract_json(raw))
                for m in parsed.get("materials", []):
                    m["trigger_process"] = process_name
                return parsed
            except ValueError:
                fallback = build_materials_from_text_or_kb(raw, {
                    "boq_item": boq_item,
                    "material_rules": L2.get("material_rules_summary", []),
                    "process_rules": [{
                        "process_name": process_name,
                        "materials_hint": [
                            x.strip()
                            for x in re.split(r"[、,，;；]", str(process.get("typical_materials", "")))
                            if x.strip()
                        ],
                    }],
                    "quota_materials": L2.get("quota_materials_summary", []),
                })
                for m in fallback.get("materials", []):
                    m["trigger_process"] = process_name
                    m.setdefault("confidence", "low")
                    m.setdefault("source", "AI文本兜底抽取")
                print(f"  Stage2 per-process [{process_name}] JSON解析失败，已从返回文本兜底抽取{len(fallback.get('materials', []))}项")
                return fallback
        except Exception as e:
            print(f"  Stage2 per-process [{process_name}] 失败: {e}")
            return {"materials": [], "error": str(e), "process": process_name}


def filter_forbidden_materials(materials: dict, strategy) -> dict:
    """剔除 PromptPlan 禁止的材料。匹配时做 identity key + 子串包含比较。"""
    forbidden_keys = set()
    forbidden_lower = set()
    for name in (strategy.forbidden_materials or []):
        key = material_identity_key(name)
        forbidden_keys.add(key)
        forbidden_lower.add(name.strip().lower())

    def _is_forbidden(mname: str) -> bool:
        """精确匹配 + token拆分匹配 + 子串包含匹配（防止复合名称绕过）"""
        mkey = material_identity_key(mname)
        mlower = mname.lower()
        if mkey in forbidden_keys or mlower in forbidden_lower:
            return True
        # 将候选名按常见分隔符拆分，逐个 token 检查（防 "铝合金/塑钢成品门" 绕过）
        raw_tokens = re.split(r'[/／、，,（）()\s]+', mname)
        for token in raw_tokens:
            token = token.strip()
            if len(token) < 2:
                continue
            tkey = material_identity_key(token)
            tlower = token.lower()
            if tkey in forbidden_keys or tlower in forbidden_lower:
                return True
            # token 级子串匹配：防 token="铝合金" 而 forbidden="铝合金门"
            for fk in forbidden_keys:
                if len(fk) < 3 or len(tkey) < 3:
                    continue
                if (tkey in fk or fk in tkey):
                    ratio = min(len(tkey), len(fk)) / max(len(tkey), len(fk))
                    if ratio >= 0.50:
                        return True
        # 模糊匹配: 一方是另一方的子串且重叠足够
        for fk in forbidden_keys:
            min_len = min(len(fk), len(mkey))
            if min_len < 4:
                continue
            if fk in mkey or mkey in fk:
                overlap_ratio = min_len / max(len(fk), len(mkey))
                if overlap_ratio >= 0.40:
                    return True
        return False

    filtered = []
    removed = []
    for m in materials.get("materials", []):
        mname = str(m.get("material_name", "")).strip()
        if _is_forbidden(mname):
            removed.append(mname)
            continue
        filtered.append(m)

    if removed:
        print(f"  按禁止列表剔除材料: {', '.join(removed[:8])}")

    # 检查是否缺失 must_include，缺失的补入
    must_set = {material_identity_key(n) for n in (strategy.must_include_materials or []) if n.strip()}
    present = {material_identity_key(m.get("material_name", "")) for m in filtered}
    missing = must_set - present
    if missing:
        # 补入缺失的必要材料（排除工艺描述类名称，如"xxx安装"）
        skip_patterns = re.compile(r'(安装|施工|运输|搬运|清理|调试)$')
        for mname in (strategy.must_include_materials or []):
            mkey = material_identity_key(mname)
            if mkey not in missing:
                continue
            if skip_patterns.search(mname):
                continue
            if "等" in mname and any(any(k in compact_text(x.get("material_name", "")) for k in ["支架", "紧固件", "槽钢", "角钢"]) for x in filtered):
                continue
            if any(k in mname for k in ["减震装置", "减振装置"]) and any(any(k in compact_text(x.get("material_name", "")) for k in ["减振", "减震", "弹簧减振器"]) for x in filtered):
                continue
            if len(mname.strip()) < 2:
                continue
            # 检查该名称的核心部分是否已被现有材料覆盖
            mcore = compact_text(re.sub(r'[（(][^)）]*[)）]', '', mname).strip())
            already_covered = False
            for existing in filtered:
                ecore = compact_text(re.sub(r'[（(][^)）]*[)）]', '', existing.get("material_name", "")).strip())
                if len(mcore) >= 2 and (mcore in ecore or ecore in mcore):
                    already_covered = True
                    break
            if already_covered:
                continue
            filtered.append({
                "material_name": mname.strip(),
                "spec_hint": "",
                "confidence": "low",
                "reason": "输入特征明示材料，知识库未覆盖，保留原始名称",
                "source": "must_include回填",
                "source_type": "must_include_fallback",
            })
            print(f"  补入缺失的必要材料: {mname.strip()}")

    return {"materials": filtered}


def merge_per_process_materials(process_results: list[dict]) -> dict:
    """合并 per-process 材料推导结果：去重、取最高置信度、T2规则优先。"""
    merged: dict[str, dict] = {}
    for result in process_results:
        for m in result.get("materials", []):
            name = m.get("material_name", "")
            if not name:
                continue
            key = material_identity_key(name)
            if key in merged:
                existing = merged[key]
                conf_order = {"high": 3, "medium": 2, "low": 1, "": 0}
                new_conf = conf_order.get(m.get("confidence", ""), 0)
                old_conf = conf_order.get(existing.get("confidence", ""), 0)
                if new_conf > old_conf:
                    merged[key] = m
                elif new_conf == old_conf and m.get("source") == "T2规则":
                    merged[key] = m
            else:
                merged[key] = m
    return {"materials": list(merged.values())}


PROCESS_OUTPUT_EXAMPLE = json.dumps(
    {
        "standard_match": {
            "matched": True,
            "standard_code": "标准清单编码",
            "standard_name": "标准清单名称",
            "t1_section": "标准分部分项/分部工程",
            "comparison_summary": "清单编码、项目名称、项目特征与标准清单的对比结论",
        },
        "processes": [
            {
                "step": 1,
                "name": "工序名称",
                "description": "工序说明",
                "typical_materials": "材料线索",
                "is_main": True,
                "evidence": "来自清单特征/标准清单/知识库的依据",
            }
        ],
    },
    ensure_ascii=False,
)

MATERIAL_OUTPUT_EXAMPLE = json.dumps(
    {
        "reasoning_summary": "基于标准清单、项目特征、工序链和知识库上下文形成的材料推理摘要",
        "materials": [
            {
                "material_name": "材料名称",
                "spec_hint": "规格线索",
                "reason": "为什么该清单项会用到该材料",
                "trigger_process": "触发工序",
                "evidence": "来自项目特征/知识库/工序的依据",
                "confidence": "high/medium/low",
                "applicability": "适用/条件适用/不适用",
            }
        ],
        "excluded_kb_materials": [
            {
                "material_name": "知识库材料名称",
                "reason": "为什么本条清单不采用该材料"
            }
        ],
    },
    ensure_ascii=False,
)

# -----------------------------
#
# 本地知识库：用于约束、检索、校验
# 支持外部 JSON 知识库目录：--kb-dir /path/to/kb
# 支持清单输入：直接运行后粘贴文本，或使用 --input 指定 txt/json/csv/xlsx
# -----------------------------
args = parse_args()

# ── 大模型厂商选择 ─────────────────────────────────────────────
PROVIDER_CONFIGS = {
    "deepseek": {
        "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "model":   os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "label":   "DeepSeek",
    },
    "ark": {
        "api_key": os.getenv("ARK_API_KEY", ""),
        "model":   os.getenv("ARK_MODEL", "doubao-seed-2-0-pro-260215"),
        "base_url": os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        "label":   "豆包/ARK",
    },
    "moonshot": {
        "api_key": os.getenv("MOONSHOT_API_KEY", ""),
        "model":   os.getenv("MOONSHOT_MODEL", "kimi-k2.6"),
        "base_url": os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1"),
        "label":   "Moonshot",
    },
}

if args.provider == "auto" and not args.sample:
    # 交互模式：手动选择
    print("\n请选择大模型厂商：")
    keys = list(PROVIDER_CONFIGS.keys())
    for i, k in enumerate(keys, 1):
        cfg = PROVIDER_CONFIGS[k]
        status = "✓ 已配置" if cfg["api_key"] else "✗ 未配置"
        print(f"  {i}. {cfg['label']} ({cfg['model']}) [{status}]")
    choice = input(f"输入数字 [1-{len(keys)}]（默认 1）: ").strip()
    try:
        idx = int(choice) - 1 if choice else 0
        if idx < 0 or idx >= len(keys):
            idx = 0
    except ValueError:
        idx = 0
    selected = keys[idx]
elif args.provider != "auto":
    selected = args.provider
else:
    selected = provider  # --sample 模式用自动检测

# 若与自动检测不同，切换密钥/模型/地址并重建客户端
if selected != provider:
    cfg = PROVIDER_CONFIGS[selected]
    if not cfg["api_key"]:
        print(f"警告: {cfg['label']} 未配置 API Key，回退到自动检测 ({provider})")
    else:
        api_key, model, base_url = cfg["api_key"], cfg["model"], cfg["base_url"]
        provider = selected
        client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
        print(f"已切换: {cfg['label']} | {mask_key(api_key)} | {model}")

BOQ_ITEMS = load_boq_items_from_input(args)
if args.parse_only:
    print(f"解析清单数: {len(BOQ_ITEMS)}")
    for idx, item in enumerate(BOQ_ITEMS[:20], start=1):
        feature_preview = compact_text(item.get("feature_text", ""))[:120]
        print(
            f"{idx}. row={item.get('source_row', '')} sheet={item.get('source_sheet', '')} "
            f"code={item.get('code', '')} name={item.get('name', '')} "
            f"unit={item.get('unit', '')} qty={item.get('quantity', '')} "
            f"section={item.get('source_section', '')}"
        )
        if feature_preview:
            print(f"   feature={feature_preview}")
    if len(BOQ_ITEMS) > 20:
        print(f"... 其余 {len(BOQ_ITEMS) - 20} 条已省略；可配合 --limit 查看更少条。")
    raise SystemExit(0)
BOQ_ITEM = BOQ_ITEMS[0] if BOQ_ITEMS else normalize_boq_item({}, fallback_args=args)
APPROVED_KB_PATH = resolve_approved_kb_path(args)
APPROVED_KB = load_approved_kb(APPROVED_KB_PATH)
print(f"人工确认知识库: {APPROVED_KB_PATH} | 已加载{len(APPROVED_KB)}条")

PROCESS_KB = [
    {
        "id": "P-G-010502001-01",
        "match_codes": ["010502001"],
        "process_name": "钢筋加工与安装",
        "order": 1,
        "required": True,
        "materials_hint": ["热轧带肋钢筋", "镀锌绑扎丝", "钢筋保护层垫块", "钢筋连接套筒", "焊剂"],
        "constraint": "现浇钢筋混凝土柱通常需要钢筋骨架，是否计入采购取决于清单是否另列钢筋项。",
    },
    {
        "id": "P-G-010502001-02",
        "match_codes": ["010502001"],
        "process_name": "模板制作与安装",
        "order": 2,
        "required": True,
        "materials_hint": ["复合木模板", "木方", "钢管支撑", "对拉螺栓", "模板脱模剂", "海绵条"],
        "constraint": "模板及支撑多为周转材料，脱模剂、密封条属于辅材或消耗材料。",
    },
    {
        "id": "P-G-010502001-03",
        "match_codes": ["010502001"],
        "process_name": "混凝土浇筑",
        "order": 3,
        "required": True,
        "materials_hint": ["预拌混凝土 C30"],
        "constraint": "清单特征为C30时，混凝土强度等级必须保持C30。",
    },
    {
        "id": "P-G-010502001-04",
        "match_codes": ["010502001"],
        "process_name": "混凝土养护",
        "order": 4,
        "required": True,
        "materials_hint": ["混凝土养护薄膜", "土工布", "混凝土养护剂"],
        "constraint": "养护材料属于辅材，是否采购取决于现场养护方式。",
    },
    {
        "id": "P-G-010502001-05",
        "match_codes": ["010502001"],
        "process_name": "模板拆除与周转",
        "order": 5,
        "required": True,
        "materials_hint": ["模板脱模剂", "模板修补材料"],
        "constraint": "拆模主要是工序动作，不应输出人工或机械。",
    },
    {
        "id": "P-D-010802001-01",
        "match_codes": ["010802001"],
        "process_name": "洞口复核与基层处理",
        "order": 1,
        "required": True,
        "materials_hint": ["基层修补砂浆"],
        "constraint": "金属门安装前应复核洞口尺寸、垂直度、预埋件或固定点条件。",
    },
    {
        "id": "P-D-010802001-02",
        "match_codes": ["010802001"],
        "process_name": "门框就位与固定",
        "order": 2,
        "required": True,
        "materials_hint": ["钢制保温门", "膨胀螺栓", "连接片"],
        "constraint": "钢制保温门安装核心材料为成品门及固定连接材料。",
    },
    {
        "id": "P-D-010802001-03",
        "match_codes": ["010802001"],
        "process_name": "门扇与五金安装",
        "order": 3,
        "required": True,
        "materials_hint": ["门锁", "合页", "把手", "闭门器", "五金配件"],
        "constraint": "项目特征已说明包括门锁、五金配件、门框门套、带保温亮窗。",
    },
    {
        "id": "P-D-010802001-04",
        "match_codes": ["010802001"],
        "process_name": "缝隙填充与密封",
        "order": 4,
        "required": True,
        "materials_hint": ["聚氨酯发泡剂", "中性硅酮密封胶"],
        "constraint": "门框与洞口之间通常需要填缝和密封，属于安装辅材。",
    },
    {
        "id": "P-D-010802001-05",
        "match_codes": ["010802001"],
        "process_name": "调试与成品保护",
        "order": 5,
        "required": True,
        "materials_hint": ["成品保护膜"],
        "constraint": "调试主要是工序动作，不应输出人工或机械。",
    },
    {
        "id": "P-W-010807001-01",
        "match_codes": ["010807001"],
        "process_name": "洞口复核与基层处理",
        "order": 1,
        "required": True,
        "materials_hint": ["基层修补材料"],
        "constraint": "金属窗安装前应复核窗洞口尺寸、垂直度、预埋件或固定点条件。",
    },
    {
        "id": "P-W-010807001-02",
        "match_codes": ["010807001"],
        "process_name": "窗框就位与固定",
        "order": 2,
        "required": True,
        "materials_hint": ["断热桥铝合金平开窗", "膨胀螺栓", "连接件"],
        "constraint": "项目特征明确为70系列断热桥铝合金平开窗，需安装窗框并固定。",
    },
    {
        "id": "P-W-010807001-03",
        "match_codes": ["010807001"],
        "process_name": "窗扇玻璃与五金安装",
        "order": 3,
        "required": True,
        "materials_hint": ["中空钢化玻璃", "窗五金配件", "手动开启装置", "不锈钢纱窗"],
        "constraint": "项目特征明确包含中空钢化玻璃、不锈钢纱窗、手动开启装置和五金配件。",
    },
    {
        "id": "P-W-010807001-04",
        "match_codes": ["010807001"],
        "process_name": "填塞缝与密封",
        "order": 4,
        "required": True,
        "materials_hint": ["聚氨酯发泡剂", "中性硅酮密封胶"],
        "constraint": "项目特征明确包含填塞缝，窗框四周通常需发泡填缝和密封胶收口。",
    },
    {
        "id": "P-W-010807001-05",
        "match_codes": ["010807001"],
        "process_name": "调试与成品保护",
        "order": 5,
        "required": True,
        "materials_hint": ["成品保护膜"],
        "constraint": "安装完成后需启闭调试、纱窗检查和成品保护。",
    }
]

MATERIAL_MAPPING_KB = [
    {
        "id": "MAP-G007",
        "match_codes": ["010502001"],
        "component": "柱",
        "material_name": "预拌混凝土 C30",
        "aliases": ["商品混凝土C30", "商砼C30", "C30混凝土", "混凝土C30", "预拌混凝土"],
        "unit": "m³",
        "coefficient": 1.01,
        "supply": "乙供",
        "required": True,
        "basis": "现浇混凝土矩形柱实体成型材料，强度等级由项目特征C30确定。",
    },
    {
        "id": "MAP-G008",
        "match_codes": ["010502001"],
        "component": "柱",
        "material_name": "热轧带肋钢筋",
        "aliases": ["螺纹钢", "HRB400钢筋", "带肋钢筋", "钢筋"],
        "unit": "t",
        "coefficient": 0.12,
        "supply": "乙供",
        "required": "conditional",
        "basis": "柱钢筋骨架材料；若钢筋清单另列，则不应重复计入混凝土柱采购主材。",
    },
    {
        "id": "MAP-G009",
        "match_codes": ["010502001"],
        "component": "柱",
        "material_name": "复合木模板",
        "aliases": ["胶合板模板", "木模板", "覆膜板", "模板"],
        "unit": "m²",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "矩形柱现浇成型需要模板围护，属于周转使用材料。",
    },
    {
        "id": "MAP-G010",
        "match_codes": ["010502001"],
        "component": "柱",
        "material_name": "模板脱模剂",
        "aliases": ["脱模剂", "隔离剂"],
        "unit": "kg",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "模板安装和周转需涂刷隔离剂，便于拆模并保护模板面。",
    },
    {
        "id": "MAP-G011",
        "match_codes": ["010502001"],
        "component": "柱",
        "material_name": "镀锌绑扎丝",
        "aliases": ["绑扎丝", "扎丝", "铁丝"],
        "unit": "kg",
        "coefficient": None,
        "supply": "乙供",
        "required": "conditional",
        "basis": "钢筋绑扎连接常用辅材；若采用机械连接或焊接仍可能局部使用。",
    },
    {
        "id": "MAP-G012",
        "match_codes": ["010502001"],
        "component": "柱",
        "material_name": "钢筋保护层垫块",
        "aliases": ["保护层垫块", "塑料垫块", "砂浆垫块", "垫块"],
        "unit": "个",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "柱钢筋保护层厚度控制需要垫块或定位件。",
    },
    {
        "id": "MAP-G013",
        "match_codes": ["010502001"],
        "component": "柱",
        "material_name": "混凝土养护薄膜",
        "aliases": ["塑料薄膜", "养护膜", "覆盖薄膜", "薄膜"],
        "unit": "m²",
        "coefficient": None,
        "supply": "乙供",
        "required": "conditional",
        "basis": "覆盖保湿养护方式下使用；若采用喷涂养护剂则可替代。",
    },
    {
        "id": "MAP-G014",
        "match_codes": ["010502001"],
        "component": "柱",
        "material_name": "混凝土养护剂",
        "aliases": ["养护剂", "成膜养护剂"],
        "unit": "kg",
        "coefficient": None,
        "supply": "乙供",
        "required": "conditional",
        "basis": "喷涂养护方式下使用，与覆盖薄膜通常二选一或按方案组合。",
    },
    {
        "id": "MAP-D001",
        "match_codes": ["010802001"],
        "component": "门",
        "material_name": "钢制保温门",
        "aliases": ["金属门", "钢制门", "保温门", "钢制保温门成品"],
        "unit": "m²",
        "coefficient": 1.0,
        "supply": "乙供",
        "required": True,
        "basis": "项目特征明确为钢制保温门，含门框、门套及带保温亮窗。",
    },
    {
        "id": "MAP-D002",
        "match_codes": ["010802001"],
        "component": "门",
        "material_name": "门锁及五金配件",
        "aliases": ["门锁", "五金配件", "合页", "把手", "闭门器"],
        "unit": "套",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "项目特征明确包含门锁、五金配件。",
    },
    {
        "id": "MAP-D003",
        "match_codes": ["010802001"],
        "component": "门",
        "material_name": "膨胀螺栓",
        "aliases": ["膨胀螺栓", "膨胀栓", "固定螺栓"],
        "unit": "套",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "门框固定通常需要膨胀螺栓或连接件。",
    },
    {
        "id": "MAP-D004",
        "match_codes": ["010802001"],
        "component": "门",
        "material_name": "聚氨酯发泡剂",
        "aliases": ["发泡剂", "聚氨酯泡沫填缝剂", "泡沫填缝剂"],
        "unit": "支",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "门框与墙体缝隙填充常用聚氨酯发泡剂。",
    },
    {
        "id": "MAP-D005",
        "match_codes": ["010802001"],
        "component": "门",
        "material_name": "中性硅酮密封胶",
        "aliases": ["密封胶", "硅酮密封胶", "中性胶"],
        "unit": "支",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "门框边缝收口与防渗密封需使用密封胶。",
    },
    {
        "id": "MAP-W001",
        "match_codes": ["010807001"],
        "component": "窗",
        "material_name": "断热桥铝合金平开窗",
        "aliases": ["金属窗", "断热桥铝合金窗", "铝合金平开窗", "70系列断热桥铝合金平开窗"],
        "unit": "m²",
        "coefficient": 1.0,
        "supply": "乙供",
        "required": True,
        "basis": "项目特征明确为70系列断热桥铝合金平开窗。",
    },
    {
        "id": "MAP-W002",
        "match_codes": ["010807001"],
        "component": "窗",
        "material_name": "中空钢化玻璃",
        "aliases": ["钢化玻璃", "中空玻璃", "5+15A+5+15A+5"],
        "unit": "m²",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "项目特征明确玻璃为中空玻璃(5+15A+5+15A+5)，且均采用钢化玻璃。",
    },
    {
        "id": "MAP-W003",
        "match_codes": ["010807001"],
        "component": "窗",
        "material_name": "不锈钢纱窗",
        "aliases": ["纱窗", "不锈钢纱窗"],
        "unit": "m²",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "项目特征明确窗开启扇带不锈钢纱窗。",
    },
    {
        "id": "MAP-W004",
        "match_codes": ["010807001"],
        "component": "窗",
        "material_name": "手动开启装置",
        "aliases": ["开启装置", "手动开启装置"],
        "unit": "套",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "项目特征明确开启扇配手动开启装置。",
    },
    {
        "id": "MAP-W005",
        "match_codes": ["010807001"],
        "component": "窗",
        "material_name": "窗五金配件",
        "aliases": ["五金配件", "窗五金", "执手", "铰链", "滑撑"],
        "unit": "套",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "项目特征明确包含五金配件及手动开启装置安装。",
    },
    {
        "id": "MAP-W006",
        "match_codes": ["010807001"],
        "component": "窗",
        "material_name": "聚氨酯发泡剂",
        "aliases": ["发泡剂", "聚氨酯泡沫填缝剂", "泡沫填缝剂", "填塞缝"],
        "unit": "支",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "项目特征明确包括填塞缝，窗框与洞口缝隙通常使用发泡剂填充。",
    },
    {
        "id": "MAP-W007",
        "match_codes": ["010807001"],
        "component": "窗",
        "material_name": "中性硅酮密封胶",
        "aliases": ["密封胶", "硅酮密封胶", "中性胶"],
        "unit": "支",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "窗框周边收口和防渗密封通常需要中性硅酮密封胶。",
    },
    {
        "id": "MAP-S001",
        "match_codes": ["010603002", "010604001"],
        "component": "钢结构",
        "material_name": "方钢 B150*5",
        "aliases": ["方钢B150*5", "方管B150*5", "方管钢柱", "方管钢梁", "B150*5"],
        "unit": "t",
        "coefficient": 1.0,
        "supply": "乙供",
        "required": True,
        "basis": "项目特征明确钢材品种、规格为方钢B150*5。",
    },
    {
        "id": "MAP-S002",
        "match_codes": ["010603002", "010604001"],
        "component": "钢结构",
        "material_name": "环氧富锌防锈底漆",
        "aliases": ["环氧富锌防锈底漆", "环氧富锌底漆"],
        "unit": "kg",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "项目特征明确涂刷环氧富锌防锈底漆2遍，锌含量≥80%。",
    },
    {
        "id": "MAP-S003",
        "match_codes": ["010603002", "010604001"],
        "component": "钢结构",
        "material_name": "环氧云铁中间漆",
        "aliases": ["环氧云铁中间漆", "云铁中间漆"],
        "unit": "kg",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "项目特征明确涂刷环氧云铁中间漆2遍。",
    },
    {
        "id": "MAP-S004",
        "match_codes": ["010603002", "010604001"],
        "component": "钢结构",
        "material_name": "室内钢结构超薄水基性防火涂料",
        "aliases": ["超薄水基性防火涂料", "钢结构防火涂料", "室内钢结构超薄水基性防火涂料"],
        "unit": "kg",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "项目特征明确室内钢结构超薄水基性防火涂料分层涂刷。",
    },
    {
        "id": "MAP-S005",
        "match_codes": ["010603002", "010604001"],
        "component": "钢结构",
        "material_name": "纯丙乳胶漆防腐面涂料",
        "aliases": ["纯丙乳胶漆", "防腐面涂料", "纯丙乳胶漆防腐面涂料"],
        "unit": "kg",
        "coefficient": None,
        "supply": "乙供",
        "required": True,
        "basis": "项目特征明确涂刷纯丙乳胶漆防腐面涂料2遍。",
    },
    {
        "id": "MAP-S006",
        "match_codes": ["010603002", "010604001"],
        "component": "钢结构",
        "material_name": "焊接材料",
        "aliases": ["焊条", "焊丝", "焊接材料"],
        "unit": "kg",
        "coefficient": None,
        "supply": "乙供",
        "required": "conditional",
        "basis": "钢柱、钢梁现场安装可能涉及焊接连接，需结合连接方式判断。",
    },
    {
        "id": "MAP-S007",
        "match_codes": ["010603002", "010604001"],
        "component": "钢结构",
        "material_name": "高强螺栓",
        "aliases": ["螺栓", "高强螺栓", "连接螺栓"],
        "unit": "套",
        "coefficient": None,
        "supply": "乙供",
        "required": "conditional",
        "basis": "钢结构安装可能采用螺栓连接，需结合设计连接方式判断。",
    }
]

MATERIAL_CLASSIFICATION_KB = [
    {
        "material_name": "预拌混凝土 C30",
        "aliases": ["商品混凝土C30", "商砼C30", "C30混凝土", "混凝土C30", "预拌混凝土"],
        "role": "主材",
        "category_l1": "混凝土材料",
        "category_l2": "预拌混凝土",
        "purchase_unit": "m³",
    },
    {
        "material_name": "热轧带肋钢筋",
        "aliases": ["螺纹钢", "HRB400钢筋", "带肋钢筋", "钢筋"],
        "role": "主材",
        "category_l1": "钢筋材料",
        "category_l2": "热轧带肋钢筋",
        "purchase_unit": "t",
    },
    {
        "material_name": "复合木模板",
        "aliases": ["胶合板模板", "木模板", "覆膜板", "模板"],
        "role": "周转材料",
        "category_l1": "模板及支撑材料",
        "category_l2": "木模板",
        "purchase_unit": "m²",
    },
    {
        "material_name": "模板脱模剂",
        "aliases": ["脱模剂", "隔离剂"],
        "role": "辅材",
        "category_l1": "模板辅材",
        "category_l2": "脱模剂",
        "purchase_unit": "kg",
    },
    {
        "material_name": "镀锌绑扎丝",
        "aliases": ["绑扎丝", "扎丝", "铁丝"],
        "role": "辅材",
        "category_l1": "钢筋辅材",
        "category_l2": "绑扎材料",
        "purchase_unit": "kg",
    },
    {
        "material_name": "钢筋保护层垫块",
        "aliases": ["保护层垫块", "塑料垫块", "砂浆垫块", "垫块"],
        "role": "辅材",
        "category_l1": "钢筋辅材",
        "category_l2": "保护层控制材料",
        "purchase_unit": "个",
    },
    {
        "material_name": "混凝土养护薄膜",
        "aliases": ["塑料薄膜", "养护膜", "覆盖薄膜", "薄膜"],
        "role": "辅材",
        "category_l1": "养护材料",
        "category_l2": "覆盖养护材料",
        "purchase_unit": "m²",
    },
    {
        "material_name": "混凝土养护剂",
        "aliases": ["养护剂", "成膜养护剂"],
        "role": "辅材",
        "category_l1": "养护材料",
        "category_l2": "喷涂养护材料",
        "purchase_unit": "kg",
    },
    {
        "material_name": "海绵条",
        "aliases": ["模板密封条", "密封条", "双面胶条"],
        "role": "辅材",
        "category_l1": "模板辅材",
        "category_l2": "拼缝密封材料",
        "purchase_unit": "m",
    },
    {
        "material_name": "钢筋连接套筒",
        "aliases": ["连接套筒", "直螺纹套筒", "套筒"],
        "role": "辅材",
        "category_l1": "钢筋辅材",
        "category_l2": "机械连接材料",
        "purchase_unit": "个",
    },
    {
        "material_name": "焊剂",
        "aliases": ["电渣压力焊焊剂", "焊粉"],
        "role": "辅材",
        "category_l1": "钢筋辅材",
        "category_l2": "焊接材料",
        "purchase_unit": "kg",
    },
    {
        "material_name": "钢制保温门",
        "aliases": ["金属门", "钢制门", "保温门", "钢制保温门成品"],
        "role": "主材",
        "category_l1": "门窗材料",
        "category_l2": "金属门/钢制门",
        "purchase_unit": "m²",
    },
    {
        "material_name": "门锁及五金配件",
        "aliases": ["门锁", "五金配件", "合页", "把手", "闭门器"],
        "role": "辅材",
        "category_l1": "门窗五金",
        "category_l2": "门用五金配件",
        "purchase_unit": "套",
    },
    {
        "material_name": "膨胀螺栓",
        "aliases": ["膨胀螺栓", "膨胀栓", "固定螺栓"],
        "role": "辅材",
        "category_l1": "连接紧固材料",
        "category_l2": "膨胀螺栓",
        "purchase_unit": "套",
    },
    {
        "material_name": "聚氨酯发泡剂",
        "aliases": ["发泡剂", "聚氨酯泡沫填缝剂", "泡沫填缝剂"],
        "role": "辅材",
        "category_l1": "填缝密封材料",
        "category_l2": "聚氨酯发泡剂",
        "purchase_unit": "支",
    },
    {
        "material_name": "中性硅酮密封胶",
        "aliases": ["密封胶", "硅酮密封胶", "中性胶"],
        "role": "辅材",
        "category_l1": "填缝密封材料",
        "category_l2": "硅酮密封胶",
        "purchase_unit": "支",
    },
    # 新增门窗材料相关条目
    {
        "material_name": "断热桥铝合金平开窗",
        "aliases": ["金属窗", "断热桥铝合金窗", "铝合金平开窗", "70系列断热桥铝合金平开窗"],
        "role": "主材",
        "category_l1": "门窗材料",
        "category_l2": "断热桥铝合金窗",
        "purchase_unit": "m²",
    },
    {
        "material_name": "中空钢化玻璃",
        "aliases": ["钢化玻璃", "中空玻璃", "5+15A+5+15A+5"],
        "role": "主材",
        "category_l1": "玻璃材料",
        "category_l2": "中空钢化玻璃",
        "purchase_unit": "m²",
    },
    {
        "material_name": "不锈钢纱窗",
        "aliases": ["纱窗", "不锈钢纱窗"],
        "role": "辅材",
        "category_l1": "门窗材料",
        "category_l2": "纱窗",
        "purchase_unit": "m²",
    },
    {
        "material_name": "手动开启装置",
        "aliases": ["开启装置", "手动开启装置"],
        "role": "辅材",
        "category_l1": "门窗五金",
        "category_l2": "开启装置",
        "purchase_unit": "套",
    },
    {
        "material_name": "窗五金配件",
        "aliases": ["五金配件", "窗五金", "执手", "铰链", "滑撑"],
        "role": "辅材",
        "category_l1": "门窗五金",
        "category_l2": "窗用五金配件",
        "purchase_unit": "套",
    },
    {
        "material_name": "方钢 B150*5",
        "aliases": ["方钢B150*5", "方管B150*5", "方管钢柱", "方管钢梁", "B150*5"],
        "role": "主材",
        "category_l1": "钢结构材料",
        "category_l2": "方钢/方管",
        "purchase_unit": "t",
    },
    {
        "material_name": "环氧富锌防锈底漆",
        "aliases": ["环氧富锌防锈底漆", "环氧富锌底漆"],
        "role": "辅材",
        "category_l1": "防腐涂料",
        "category_l2": "环氧富锌底漆",
        "purchase_unit": "kg",
    },
    {
        "material_name": "环氧云铁中间漆",
        "aliases": ["环氧云铁中间漆", "云铁中间漆"],
        "role": "辅材",
        "category_l1": "防腐涂料",
        "category_l2": "环氧云铁中间漆",
        "purchase_unit": "kg",
    },
    {
        "material_name": "室内钢结构超薄水基性防火涂料",
        "aliases": ["超薄水基性防火涂料", "钢结构防火涂料", "室内钢结构超薄水基性防火涂料"],
        "role": "辅材",
        "category_l1": "防火涂料",
        "category_l2": "钢结构防火涂料",
        "purchase_unit": "kg",
    },
    {
        "material_name": "纯丙乳胶漆防腐面涂料",
        "aliases": ["纯丙乳胶漆", "防腐面涂料", "纯丙乳胶漆防腐面涂料"],
        "role": "辅材",
        "category_l1": "防腐涂料",
        "category_l2": "面涂料",
        "purchase_unit": "kg",
    },
    {
        "material_name": "焊接材料",
        "aliases": ["焊条", "焊丝", "焊接材料"],
        "role": "辅材",
        "category_l1": "焊接材料",
        "category_l2": "焊条/焊丝",
        "purchase_unit": "kg",
    },
    {
        "material_name": "高强螺栓",
        "aliases": ["螺栓", "高强螺栓", "连接螺栓"],
        "role": "辅材",
        "category_l1": "连接紧固材料",
        "category_l2": "高强螺栓",
        "purchase_unit": "套",
    }
]

LOSS_RATE_KB = [
    {
        "material_name": "预拌混凝土 C30",
        "aliases": ["商品混凝土C30", "商砼C30", "C30混凝土", "混凝土C30", "预拌混凝土"],
        "loss_rate": 0.02,
        "loss_basis": "预拌混凝土运输、泵送、浇筑综合损耗。",
    },
    {
        "material_name": "热轧带肋钢筋",
        "aliases": ["螺纹钢", "HRB400钢筋", "带肋钢筋", "钢筋"],
        "loss_rate": 0.025,
        "loss_basis": "钢筋下料、加工、连接、绑扎综合损耗。",
    },
    {
        "material_name": "复合木模板",
        "aliases": ["胶合板模板", "木模板", "覆膜板", "模板"],
        "loss_rate": 0.0,
        "loss_basis": "周转材料不按一次性消耗损耗计入，需按摊销规则另算。",
    },
    {
        "material_name": "模板脱模剂",
        "aliases": ["脱模剂", "隔离剂"],
        "loss_rate": 0.03,
        "loss_basis": "涂刷、污染、残留及现场损耗。",
    },
    {
        "material_name": "镀锌绑扎丝",
        "aliases": ["绑扎丝", "扎丝", "铁丝"],
        "loss_rate": 0.02,
        "loss_basis": "绑扎裁剪及现场散落损耗。",
    },
    {
        "material_name": "钢筋保护层垫块",
        "aliases": ["保护层垫块", "塑料垫块", "砂浆垫块", "垫块"],
        "loss_rate": 0.03,
        "loss_basis": "安装破损、丢失及现场损耗。",
    },
    {
        "material_name": "混凝土养护薄膜",
        "aliases": ["塑料薄膜", "养护膜", "覆盖薄膜", "薄膜"],
        "loss_rate": 0.05,
        "loss_basis": "裁剪、破损、覆盖搭接损耗。",
    },
    {
        "material_name": "混凝土养护剂",
        "aliases": ["养护剂", "成膜养护剂"],
        "loss_rate": 0.03,
        "loss_basis": "喷涂、残留及现场损耗。",
    },
    {
        "material_name": "海绵条",
        "aliases": ["模板密封条", "密封条", "双面胶条"],
        "loss_rate": 0.05,
        "loss_basis": "模板拼缝裁剪、压损及现场损耗。",
    },
    {
        "material_name": "钢筋连接套筒",
        "aliases": ["连接套筒", "直螺纹套筒", "套筒"],
        "loss_rate": 0.01,
        "loss_basis": "机械连接安装、错配及现场损耗。",
    },
    {
        "material_name": "焊剂",
        "aliases": ["电渣压力焊焊剂", "焊粉"],
        "loss_rate": 0.03,
        "loss_basis": "焊接作业消耗、残留及现场损耗。",
    },
    {
        "material_name": "钢制保温门",
        "aliases": ["金属门", "钢制门", "保温门", "钢制保温门成品"],
        "loss_rate": 0.0,
        "loss_basis": "成品门按设计洞口或清单面积计量，通常不另计材料损耗。",
    },
    {
        "material_name": "门锁及五金配件",
        "aliases": ["门锁", "五金配件", "合页", "把手", "闭门器"],
        "loss_rate": 0.01,
        "loss_basis": "五金配件按套配置，考虑现场安装损耗和替换余量。",
    },
    {
        "material_name": "膨胀螺栓",
        "aliases": ["膨胀螺栓", "膨胀栓", "固定螺栓"],
        "loss_rate": 0.02,
        "loss_basis": "钻孔固定、错孔、损坏及现场散落损耗。",
    },
    {
        "material_name": "聚氨酯发泡剂",
        "aliases": ["发泡剂", "聚氨酯泡沫填缝剂", "泡沫填缝剂"],
        "loss_rate": 0.05,
        "loss_basis": "填缝施工存在残留、溢出切割和喷嘴损耗。",
    },
    {
        "material_name": "中性硅酮密封胶",
        "aliases": ["密封胶", "硅酮密封胶", "中性胶"],
        "loss_rate": 0.05,
        "loss_basis": "施胶存在挤出残留、修边和接缝损耗。",
    },
    # 新增门窗材料损耗条目
    {
        "material_name": "断热桥铝合金平开窗",
        "aliases": ["金属窗", "断热桥铝合金窗", "铝合金平开窗", "70系列断热桥铝合金平开窗"],
        "loss_rate": 0.0,
        "loss_basis": "成品窗按设计洞口或清单面积计量，通常不另计材料损耗。",
    },
    {
        "material_name": "中空钢化玻璃",
        "aliases": ["钢化玻璃", "中空玻璃", "5+15A+5+15A+5"],
        "loss_rate": 0.02,
        "loss_basis": "玻璃安装、运输、破损及现场替换综合损耗。",
    },
    {
        "material_name": "不锈钢纱窗",
        "aliases": ["纱窗", "不锈钢纱窗"],
        "loss_rate": 0.01,
        "loss_basis": "纱窗按开启扇配置，考虑现场安装损耗和替换余量。",
    },
    {
        "material_name": "手动开启装置",
        "aliases": ["开启装置", "手动开启装置"],
        "loss_rate": 0.01,
        "loss_basis": "开启装置按套配置，考虑安装损耗和备件余量。",
    },
    {
        "material_name": "窗五金配件",
        "aliases": ["五金配件", "窗五金", "执手", "铰链", "滑撑"],
        "loss_rate": 0.01,
        "loss_basis": "窗五金按套配置，考虑现场安装损耗和替换余量。",
    },
    {
        "material_name": "方钢 B150*5",
        "aliases": ["方钢B150*5", "方管B150*5", "方管钢柱", "方管钢梁", "B150*5"],
        "loss_rate": 0.02,
        "loss_basis": "钢结构构件下料、切割、加工、安装综合损耗。",
    },
    {
        "material_name": "环氧富锌防锈底漆",
        "aliases": ["环氧富锌防锈底漆", "环氧富锌底漆"],
        "loss_rate": 0.05,
        "loss_basis": "钢结构涂装存在涂刷、滚涂、残留和现场损耗。",
    },
    {
        "material_name": "环氧云铁中间漆",
        "aliases": ["环氧云铁中间漆", "云铁中间漆"],
        "loss_rate": 0.05,
        "loss_basis": "钢结构中间漆涂装存在涂刷、滚涂、残留和现场损耗。",
    },
    {
        "material_name": "室内钢结构超薄水基性防火涂料",
        "aliases": ["超薄水基性防火涂料", "钢结构防火涂料", "室内钢结构超薄水基性防火涂料"],
        "loss_rate": 0.06,
        "loss_basis": "防火涂料分层涂刷存在基层吸附、涂刷、残留和现场损耗。",
    },
    {
        "material_name": "纯丙乳胶漆防腐面涂料",
        "aliases": ["纯丙乳胶漆", "防腐面涂料", "纯丙乳胶漆防腐面涂料"],
        "loss_rate": 0.05,
        "loss_basis": "面涂料涂刷存在涂刷、滚涂、残留和现场损耗。",
    },
    {
        "material_name": "焊接材料",
        "aliases": ["焊条", "焊丝", "焊接材料"],
        "loss_rate": 0.03,
        "loss_basis": "焊接作业消耗、焊渣、残留和现场损耗。",
    },
    {
        "material_name": "高强螺栓",
        "aliases": ["螺栓", "高强螺栓", "连接螺栓"],
        "loss_rate": 0.01,
        "loss_basis": "螺栓安装错配、损坏和备件余量。",
    }
]

FORBIDDEN_OUTPUTS = ["人工", "工日", "机械", "泵车", "振捣棒", "运输服务", "吊运服务", "施工服务"]

def is_standard_code_match(boq_item: dict, rule: dict) -> bool:
    code = str(boq_item.get("code", ""))
    standard_code = str(boq_item.get("standard_code", ""))
    match_codes = [str(x) for x in rule.get("match_codes", [])]
    return (
        code in match_codes
        or standard_code in match_codes
        or any(code.startswith(x) for x in match_codes if x)
        or any(standard_code.startswith(x) for x in match_codes if x)
    )

def retrieve_kb_context(boq_item: dict) -> dict:
    code = boq_item.get("code", "")
    component = boq_item.get("component", "")
    name = boq_item.get("name", "")
    feature_text = boq_item.get("feature_text", "")
    text = f"{code} {component} {name} {feature_text}"

    process_rules = [r for r in PROCESS_KB if is_standard_code_match(boq_item, r)]

    material_rules = []
    for r in MATERIAL_MAPPING_KB:
        aliases = r.get("aliases", [])
        if is_standard_code_match(boq_item, r):
            material_rules.append(r)
        elif any(alias and alias in text for alias in aliases):
            material_rules.append(r)
        elif r.get("material_name", "") and r.get("material_name", "") in text:
            material_rules.append(r)

    # ── 合并硬编码KB与T3动态KB，构建全量分类/损耗知识库 ──
    # T3动态库提供全面覆盖（330种物料），硬编码KB保留已精调的规则
    _t3_class_kb = build_classification_kb_from_t3()
    _t3_class_names = {compact_text(r["material_name"]) for r in _t3_class_kb}
    # 硬编码规则优先：同名物料保留硬编码版本
    _hard_class_names = {compact_text(r["material_name"]) for r in MATERIAL_CLASSIFICATION_KB}
    _merged_class_kb = MATERIAL_CLASSIFICATION_KB + [
        r for r in _t3_class_kb
        if compact_text(r["material_name"]) not in _hard_class_names
    ]

    _t3_loss_kb = build_loss_kb_from_t3()
    _t3_loss_names = {compact_text(r["material_name"]) for r in _t3_loss_kb}
    _hard_loss_names = {compact_text(r["material_name"]) for r in LOSS_RATE_KB}
    _merged_loss_kb = LOSS_RATE_KB + [
        r for r in _t3_loss_kb
        if compact_text(r["material_name"]) not in _hard_loss_names
    ]

    classification_rules = []
    for r in _merged_class_kb:
        aliases = r.get("aliases", [])
        if any(alias and alias in text for alias in aliases):
            classification_rules.append(r)
        elif r.get("material_name", "") and r.get("material_name", "") in text:
            classification_rules.append(r)

    loss_rules = []
    for r in _merged_loss_kb:
        aliases = r.get("aliases", [])
        if any(alias and alias in text for alias in aliases):
            loss_rules.append(r)
        elif r.get("material_name", "") and r.get("material_name", "") in text:
            loss_rules.append(r)

    for rule in material_rules:
        material_name = rule.get("material_name", "")
        for class_rule in _merged_class_kb:
            names = [class_rule.get("material_name", "")] + class_rule.get("aliases", [])
            if material_name in names and class_rule not in classification_rules:
                classification_rules.append(class_rule)
        for loss_rule in _merged_loss_kb:
            names = [loss_rule.get("material_name", "")] + loss_rule.get("aliases", [])
            if material_name in names and loss_rule not in loss_rules:
                loss_rules.append(loss_rule)

    if is_integrated_hvac_equipment(boq_item):
        classification_rules = [
            r for r in classification_rules
            if "风机" not in compact_text(r.get("material_name", ""))
        ]
        loss_rules = [
            r for r in loss_rules
            if "风机" not in compact_text(r.get("material_name", ""))
        ]

    quota_context = quota_context_for_standard(boq_item.get("standard_code", ""), name, feature_text)
    q0_process_rule = None
    if boq_item.get("standard_work_items"):
        q0_process_rule = {
            "id": f"Q0-{boq_item.get('standard_code', '')}",
            "process_name": "国标清单工作内容",
            "required": True,
            "materials_hint": extract_material_hints(feature_text + "\n" + boq_item.get("work_scope", "")),
            "constraint": "；".join(boq_item.get("standard_work_items", [])),
        }

    # 泛化兜底：若知识库未命中，仍提供约束，不让 AI 裸奔。
    generic_process_constraints = []
    if not process_rules:
        if q0_process_rule:
            generic_process_constraints.append(q0_process_rule)
        else:
            generic_process_constraints.append(
                {
                    "id": "GENERIC-PROCESS",
                    "process_name": "AI根据清单名称与项目特征推理工序",
                    "required": "unknown",
                    "materials_hint": [],
                    "constraint": "知识库未命中具体工序规则，AI必须基于清单名称、项目特征和工程常识推理，不得输出与清单无关工序。",
                }
            )

    approved_rules = find_approved_rules(boq_item, APPROVED_KB)
    return {
        "boq_item": boq_item,
        "process_rules": sorted(process_rules, key=lambda x: x.get("order", 999)) or generic_process_constraints,
        "material_rules": material_rules,
        "classification_rules": classification_rules,
        "loss_rules": loss_rules,
        "approved_rules": approved_rules,
        "quota_candidates": quota_context.get("quota_candidates", []),
        "quota_materials": quota_context.get("quota_materials", []),
        "forbidden_outputs": FORBIDDEN_OUTPUTS,
        "constraints": [
            "输出材料必须能被清单特征、施工工序或材料映射规则支撑。",
            "清单编码必须先按 Q0 国标编码命中；若有顺序码，回退到国标项目编码后再推理。",
            "实际项目特征必须与 Q0 标准项目特征、工作内容做对比，说明新增、缺失或改变的材料/工序。",
            "Q1/Q2 定额材料消耗只能作为候选依据；若定额规格与实际特征不一致，必须以实际特征为准。",
            "禁止输出人工、机械、运输服务。",
            "强度等级、材料类型、采购单位必须优先服从知识库规则。",
            "材料分类必须以后置 classification_rules 为准，AI 不直接决定。",
            "材料损耗必须以后置 loss_rules 为准，AI 不直接决定。",
            "知识库中 required=True 的材料若未输出，必须在校验阶段提示缺失。",
            "conditional 材料可输出，但必须说明触发工序或适用条件。",
            "若 material_rules 为空，AI可以补充推理材料，但必须标记 source=AI推理-知识库未命中 且 confidence 不得为 high。",
        ],
    }

def compact_kb_for_prompt(kb_context: dict) -> str:
    payload = {
        "boq_item": kb_context["boq_item"],
        "q0_match": kb_context["boq_item"].get("q0_match", {}),
        "standard_boq": {
            "standard_code": kb_context["boq_item"].get("standard_code", ""),
            "standard_name": kb_context["boq_item"].get("standard_name", ""),
            "standard_features": kb_context["boq_item"].get("standard_features", []),
            "standard_work_items": kb_context["boq_item"].get("standard_work_items", []),
            "standard_unit": kb_context["boq_item"].get("standard_unit", ""),
            "calculation_rule": kb_context["boq_item"].get("calculation_rule", ""),
        },
        "standard_feature_comparison": compare_standard_features(
            kb_context["boq_item"].get("standard_features", []),
            kb_context["boq_item"].get("feature_text", ""),
        ),
        "actual_material_hints": extract_material_hints(
            kb_context["boq_item"].get("name", "") + "\n" + kb_context["boq_item"].get("feature_text", "")
        ),
        "quota_candidates": kb_context.get("quota_candidates", [])[:8],
        "quota_materials": kb_context.get("quota_materials", [])[:30],
        "process_rules": [
            {
                "id": r["id"],
                "process_name": r["process_name"],
                "required": r["required"],
                "materials_hint": r["materials_hint"],
                "constraint": r["constraint"],
            }
            for r in kb_context["process_rules"]
        ],
        "material_rules": [
            {
                "id": r["id"],
                "material_name": r["material_name"],
                "aliases": r["aliases"],
                "required": r["required"],
                "basis": r["basis"],
            }
            for r in kb_context["material_rules"]
        ],
        "classification_rules": [
            {
                "material_name": r["material_name"],
                "aliases": r["aliases"],
                "role": r["role"],
                "category_l1": r["category_l1"],
                "category_l2": r["category_l2"],
                "purchase_unit": r["purchase_unit"],
            }
            for r in kb_context["classification_rules"]
        ],
        "loss_rules": [
            {
                "material_name": r["material_name"],
                "aliases": r["aliases"],
                "loss_rate": r["loss_rate"],
                "loss_basis": r["loss_basis"],
            }
            for r in kb_context["loss_rules"]
        ],
        "approved_rules": [
            {
                "id": r.get("id", ""),
                "standard_code": r.get("standard_code", ""),
                "name": r.get("name", ""),
                "feature_text": r.get("feature_text", ""),
                "approved_materials": [m.get("material_name", "") for m in r.get("approved_materials", [])],
                "rejected_materials": r.get("rejected_materials", []),
                "user_note": r.get("user_note", ""),
            }
            for r in kb_context.get("approved_rules", [])
        ],
        "project_defaults": kb_context.get("project_defaults", {}),
        "forbidden_outputs": kb_context["forbidden_outputs"],
        "constraints": kb_context["constraints"],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
def match_kb_record(material_name: str, kb_records: list):
    name = compact_text(material_name)
    for record in kb_records:
        if compact_text(record.get("material_name", "")) == name:
            return record
    for record in kb_records:
        aliases = [compact_text(x) for x in record.get("aliases", []) if compact_text(x)]
        if name in aliases:
            return record
    best_record, best_len = None, 0
    for record in kb_records:
        names = [record.get("material_name", "")] + record.get("aliases", [])
        for alias in names:
            alias_norm = compact_text(alias)
            if len(alias_norm) < 2:
                continue
            if alias_norm in name or name in alias_norm:
                if len(alias_norm) > best_len:
                    best_record, best_len = record, len(alias_norm)
    return best_record


def material_standard_index() -> dict:
    return {r.get("standard_id", ""): r for r in n1_standard_rows()}


def extract_required_standard_params(material_id: str, t3_row: dict) -> list[dict]:
    params = []
    seen = set()
    for p in parse_spec_pattern(t3_row.get("规格模式JSON", "")):
        if not isinstance(p, dict):
            continue
        if p.get("required") is True:
            key = (p.get("param", ""), p.get("source_standard", ""), p.get("source_clause", ""))
            if key in seen:
                continue
            seen.add(key)
            params.append({
                "param": p.get("param", ""),
                "unit": p.get("unit", ""),
                "default": p.get("default", ""),
                "standard_id": p.get("source_standard", ""),
                "clause_id": p.get("source_clause", ""),
                "allowed_values": p.get("values", [])[:12] if isinstance(p.get("values"), list) else [],
            })
    for r in n3_param_rows():
        if r.get("material_id") != material_id or r.get("required_level") != "required":
            continue
        key = (r.get("param_name", ""), r.get("standard_id", ""), r.get("clause_id", ""))
        if key in seen:
            continue
        seen.add(key)
        params.append({
            "param": r.get("param_name", ""),
            "unit": r.get("unit", ""),
            "default": r.get("default_value", ""),
            "standard_id": r.get("standard_id", ""),
            "clause_id": r.get("clause_id", ""),
            "allowed_values": [],
        })
    return params[:8]


def get_material_standard_refs(material_id: str, fallback_standard_code: str = "") -> list[dict]:
    n1_idx = material_standard_index()
    refs = []
    seen = set()
    for r in n5_material_standard_rows():
        if r.get("material_id") != material_id or r.get("active_flag", "1") not in {"1", "true", "True"}:
            continue
        sid = r.get("standard_id", "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        std = n1_idx.get(sid, {})
        refs.append({
            "standard_id": sid,
            "standard_name": std.get("standard_name", ""),
            "relation_type": r.get("relation_type", ""),
            "relevance": r.get("relevance", ""),
            "use_scene": r.get("use_scene", ""),
            "clause_refs": parse_json_list_field(r.get("clause_refs", "")),
            "status": std.get("status", ""),
        })
    if fallback_standard_code and fallback_standard_code not in seen:
        std = n1_idx.get(fallback_standard_code, {})
        refs.append({
            "standard_id": fallback_standard_code,
            "standard_name": std.get("standard_name", ""),
            "relation_type": "product_standard",
            "relevance": "primary",
            "use_scene": "规格定义/质量验收",
            "clause_refs": [],
            "status": std.get("status", ""),
        })
    return refs[:6]


def format_standard_refs(refs: list[dict], fallback_standard_code: str = "") -> str:
    codes = []
    for ref in refs or []:
        sid = ref.get("standard_id", "")
        if sid and sid not in codes:
            codes.append(sid)
    if fallback_standard_code and fallback_standard_code not in codes:
        codes.append(fallback_standard_code)
    return "、".join(codes[:4])


def normalize_material_match_text(text: str) -> str:
    text = compact_text(text)
    # 建筑清单里“粘接/粘结/胶粘”经常混写，匹配时归一；输出仍以T3标准名称为准。
    return (
        text.replace("粘接", "粘结")
        .replace("胶粘", "粘结")
        .replace("砼", "混凝土")
        .replace("：", ":")
    )


GENERIC_MATCH_TERMS = {
    "水", "砂", "土", "门", "胶", "膏",
    "粘结剂", "胶粘剂", "专用粘结剂", "密封材料", "辅材",
}


def is_generic_match_term(term: str) -> bool:
    term = normalize_material_match_text(term)
    return len(term) < 2 or term in GENERIC_MATCH_TERMS


def scope_project_matches(scope_projects: list[str], boq_text: str) -> bool:
    if not scope_projects:
        return False
    normalized_scopes = [normalize_material_match_text(x) for x in scope_projects if normalize_material_match_text(x)]
    if not normalized_scopes:
        return False
    if "全部" in normalized_scopes:
        return False
    for scope in normalized_scopes:
        if scope in boq_text:
            return True
        if "外墙" in scope and ("外墙" in boq_text or "墙面" in boq_text):
            return True
        if "地面" in scope and any(x in boq_text for x in ["散水", "坡道", "地坪", "面层"]):
            return True
        if "灰土" in scope and "灰土" in boq_text:
            return True
        if "保温" in scope and "保温" in boq_text:
            return True
        if ("伸缩缝" in scope or "接缝" in scope) and any(x in boq_text for x in ["嵌缝", "分隔缝", "变形缝", "接缝"]):
            return True
    return False


def score_t3_material_match(candidate_name: str, t3_row: dict, boq_item: dict) -> int:
    candidate = normalize_material_match_text(candidate_name)
    boq_text = compact_text(
        f"{boq_item.get('name', '')} {boq_item.get('feature_text', '')} "
        f"{boq_item.get('t1_section', '')} {boq_item.get('standard_name', '')}"
    )
    boq_text = normalize_material_match_text(boq_text)
    std_name = normalize_material_match_text(t3_row.get("标准名称", ""))
    aliases = [normalize_material_match_text(x) for x in parse_json_list_field(t3_row.get("别名", ""))]
    feature_keywords = [normalize_material_match_text(x) for x in parse_json_list_field(t3_row.get("特征关键词", ""))]
    exclude_keywords = [compact_text(x) for x in parse_json_list_field(t3_row.get("排除关键词", ""))]
    scope_appendix = [compact_text(x) for x in parse_json_list_field(t3_row.get("适用范围(附录)", ""))]
    scope_projects = [compact_text(x) for x in parse_json_list_field(t3_row.get("适用范围(项目)", ""))]

    identity_score = 0
    if candidate == std_name:
        identity_score += 120
    elif candidate and (candidate in std_name or std_name in candidate):
        identity_score += 90
    for alias in aliases:
        if not alias:
            continue
        if candidate == alias:
            identity_score += 78 if is_generic_match_term(alias) else 105
        elif not is_generic_match_term(alias) and (candidate in alias or alias in candidate):
            identity_score += 78
    for kw in feature_keywords:
        if kw and kw in candidate:
            identity_score += 20 if is_generic_match_term(kw) else 45

    # 组合材料/俗称的保守识别：只把候选名称本身作为“身份”依据，整条清单文本不能替代名称匹配。
    if "灰土" in candidate and any("灰土" in kw for kw in feature_keywords):
        identity_score += 55
    if "密封膏" in candidate and ("油膏" in std_name or "密封胶" in std_name):
        identity_score += 55
    if "挤塑" in candidate and ("挤塑" in std_name or "XPS" in std_name.upper()):
        identity_score += 55
    if "网格布" in candidate and ("网格布" in std_name or any("网格布" in a for a in aliases)):
        identity_score += 55

    score = identity_score
    for kw in exclude_keywords:
        kw_norm = normalize_material_match_text(kw)
        if kw_norm and kw_norm in candidate:
            score -= 100

    if "幕墙" in std_name and "幕墙" not in boq_text:
        score -= 120
    if "幕墙" in "".join(scope_projects) and "幕墙" not in boq_text:
        score -= 60

    # 规格强约束：C20/C30、HRB400 等不能错配。
    cand_grade = re.findall(r"C\d+|HRB\d+E?|HPB\d+", candidate, re.I)
    std_grade = re.findall(r"C\d+|HRB\d+E?|HPB\d+", std_name, re.I)
    if cand_grade and std_grade:
        if any(a.upper() == b.upper() for a in cand_grade for b in std_grade):
            score += 45
        else:
            score -= 80

    if identity_score < 50:
        return score

    for kw in feature_keywords:
        if kw and kw in boq_text and kw not in candidate:
            score += 8

    t1 = compact_text(boq_item.get("t1_section", ""))
    if scope_appendix and any(s and (s in t1 or t1 in s or s in boq_text) for s in scope_appendix):
        score += 18
    scope_matched = scope_project_matches(scope_projects, boq_text)
    generic_candidate = any(x in candidate for x in ["粘结剂", "胶粘剂", "密封膏", "密封胶", "门"])
    if scope_projects and scope_matched:
        score += 24
    elif scope_projects:
        score -= 80 if generic_candidate else 18
    return score


def match_t3_material(candidate_name: str, boq_item: dict) -> tuple[dict | None, int]:
    best, best_score = None, -999
    for row in t3_catalog_rows():
        score = score_t3_material_match(candidate_name, row, boq_item)
        if score > best_score:
            best, best_score = row, score
    if best and best_score >= 70:
        return best, best_score
    return None, best_score


def apply_t3_standardization(item: dict, kb_context: dict) -> tuple[dict, list]:
    issues = []
    raw_name = item.get("material_name", "")
    boq_item = kb_context.get("boq_item", {})

    # 特征明示材料不强制要求 T3 匹配，不因未命中而降级
    if item.get("source_type") == "feature_explicit":
        item.setdefault("standardization_status", "feature_explicit")
        item["needs_name_review"] = False
        item["confidence"] = "high"
        t3_row, score = match_t3_material(raw_name, boq_item)
        if t3_row and score >= 70:
            item["standardization_status"] = "matched"
            item["standardization_score"] = score
            item["category_l1"] = t3_row.get("分类(一级)", item.get("category_l1", ""))
            item["category_l2"] = t3_row.get("分类(二级)", item.get("category_l2", ""))
            item["unit"] = t3_row.get("采购单位") or item.get("unit", "")
        return item, issues
    candidate_names = [raw_name, item.get("_name_before_spec_split", ""), item.get("input_material_name", "")]
    if item.get("spec_hint"):
        candidate_names.extend([
            f"{item.get('spec_hint')}{raw_name}",
            f"{raw_name} {item.get('spec_hint')}",
        ])
    strength = boq_item.get("concrete_strength", "")
    if strength and "混凝土" in compact_text(raw_name):
        candidate_names.extend([f"预拌混凝土 {strength}", f"{strength}混凝土"])
    best_row, best_score = None, -999
    for candidate in dict.fromkeys(x for x in candidate_names if x):
        row, candidate_score = match_t3_material(candidate, boq_item)
        if row and (best_row is None or candidate_score > best_score):
            best_row, best_score = row, candidate_score
        elif best_row is None and candidate_score > best_score:
            best_score = candidate_score
    t3_row, score = best_row, best_score
    if not t3_row:
        item["standardization_status"] = "unmatched"
        item["standardization_score"] = score
        item["needs_name_review"] = True
        issues.append(f"{raw_name} 未匹配到T3标准物料，不能作为已标准化采购名称。")
        return item, issues

    original = item.get("material_name", "")
    material_id = t3_row.get("物料ID", "")
    item["raw_material_name"] = original
    t3_standard_name = t3_row.get("标准名称", original)
    item["t3_standard_name"] = t3_standard_name
    core_name, spec_hint, specs = split_spec_from_name(t3_standard_name, item.get("spec_hint", ""))
    item["material_name"] = core_name or t3_standard_name
    if spec_hint:
        item["spec_hint"] = spec_hint
    if specs:
        existing_specs = item.get("extracted_specs", [])
        item["extracted_specs"] = existing_specs + [s for s in specs if s not in existing_specs]
    if "玻璃" in compact_text(item.get("material_name", "")):
        feature_glass_spec = extract_glass_spec_from_feature(boq_item.get("feature_text", ""))
        if feature_glass_spec:
            item["spec_hint"] = feature_glass_spec
            existing_specs = item.get("extracted_specs", [])
            spec_row = {"value": feature_glass_spec, "field": "glass_composition", "desc": "项目特征明示中空玻璃组成"}
            if spec_row not in existing_specs:
                item["extracted_specs"] = existing_specs + [spec_row]
    item["material_id"] = material_id
    item["t3_name"] = t3_standard_name
    item["standardization_status"] = "matched"
    item["standardization_score"] = score
    item["standard_code"] = t3_row.get("标准代号", "")
    item["standard_refs"] = get_material_standard_refs(material_id, t3_row.get("标准代号", ""))
    if not format_standard_refs(item.get("standard_refs", []), item.get("standard_code", "")):
        item["needs_standard_review"] = True
        issues.append(f"{item['material_name']} 已匹配T3标准物料，但未绑定国家/行业材料标准，需补充规范依据。")
    else:
        item["needs_standard_review"] = False
    item["required_standard_params"] = extract_required_standard_params(material_id, t3_row)
    item["category_l1"] = t3_row.get("分类(一级)", item.get("category_l1", ""))
    item["category_l2"] = t3_row.get("分类(二级)", item.get("category_l2", ""))
    item["category_l3"] = t3_row.get("分类(三级)", "")
    item["category_path"] = t3_row.get("分类路径", "")
    item["unit"] = t3_row.get("采购单位") or item.get("unit", "")
    if t3_row.get("品类标准损耗率"):
        try:
            item["waste_rate_estimate"] = float(t3_row.get("品类标准损耗率"))
            item["loss_basis"] = f"T3品类标准损耗率，标准物料 {material_id}"
        except Exception:
            pass
    if not item.get("source") or item.get("source") == "AI推理-知识库未命中":
        item["source"] = "T3标准物料库"

    scope_projects = parse_json_list_field(t3_row.get("适用范围(项目)", ""))
    if scope_projects:
        boq_text = normalize_material_match_text(f"{boq_item.get('name', '')} {boq_item.get('feature_text', '')} {boq_item.get('standard_name', '')}")
        if not scope_project_matches(scope_projects, boq_text):
            issues.append(f"{item['material_name']} 已匹配T3，但适用范围未直接命中本清单特征，需复核。")
            item["needs_name_review"] = True
        else:
            item["needs_name_review"] = False
    else:
        item["needs_name_review"] = False
    if any(x in item.get("material_name", "") for x in ["/", "、"]):
        item["needs_name_review"] = True
        item["combo_name"] = True
        item["combo_candidates"] = [
            x.strip() for x in re.split(r"[/／、]", item.get("material_name", ""))
            if x.strip()
        ]
        issues.append(f"{item['material_name']} 为组合型标准物料名称，采购前需拆成明确材料名。")
    return item, issues


def _try_direct_t3_classification(material_name: str) -> dict | None:
    """当文本匹配未命中分类知识库时，直接在 T3 全量库中模糊查找。
    用于确保 AI 返回的材料名即使没有出现在 boq 特征原文中也能获得三级分类。
    """
    name = compact_text(material_name)
    if len(name) < 2:
        return None

    best, best_score = None, 0
    for rule in build_classification_kb_from_t3():
        score = 0
        rule_name = compact_text(rule["material_name"])
        # 精确匹配
        if name == rule_name:
            score = 100
        # 包含关系
        elif rule_name in name or name in rule_name:
            score = 75
        # 别名匹配
        else:
            for alias in rule.get("aliases", []):
                alias_norm = compact_text(alias)
                if alias_norm and (alias_norm in name or name in alias_norm):
                    score = 70
                    break
        if score > best_score:
            best, best_score = rule, score

    if best and best_score >= 50:
        return best
    return None


def infer_fallback_material_defaults(material_name: str, boq_item: dict) -> dict:
    """知识库未覆盖时，基于实际特征给出可复核的最低限度单位/分类/损耗兜底。"""
    name = compact_text(material_name)
    feature = compact_text(boq_item.get("feature_text", ""))
    boq_unit = boq_item.get("unit") or boq_item.get("standard_unit") or ""
    defaults = {
        "role": "待复核材料",
        "category_l1": "",
        "category_l2": "",
        "unit": "",
        "waste_rate_estimate": None,
        "loss_basis": "知识库未命中，按实际项目特征临时兜底，需人工复核。",
        "coefficient": None,
        "supply": "不确定",
    }
    if "挤塑板" in name or "保温板" in name:
        defaults.update({
            "role": "主材",
            "category_l1": "保温材料",
            "category_l2": "板状保温材料",
            "unit": boq_unit or "m²",
            "waste_rate_estimate": 0.02,
            "loss_basis": "保温板按面积采购，临时按裁切、拼缝和破损损耗2%估算。",
            "coefficient": 1.0,
            "supply": "乙供",
        })
    elif "网格布" in name:
        defaults.update({
            "role": "增强材料",
            "category_l1": "保温辅材",
            "category_l2": "增强网布",
            "unit": boq_unit or "m²",
            "waste_rate_estimate": 0.05,
            "loss_basis": "网格布按搭接、裁剪和破损损耗临时按5%估算。",
            "coefficient": 2.0 if "两层" in feature or "2层" in feature else 1.0,
            "supply": "乙供",
        })
    elif "砂浆" in name or "粘接剂" in name or "粘结剂" in name or "石膏" in name or "界面剂" in name:
        defaults.update({
            "role": "辅材",
            "category_l1": "砂浆及粘结材料",
            "category_l2": "保温系统辅材",
            "unit": "kg",
            "waste_rate_estimate": 0.03,
            "loss_basis": "粉料/胶粘材料按拌制、涂抹残留和现场损耗临时按3%估算。",
            "coefficient": None,
            "supply": "乙供",
        })
    elif "混凝土" in name:
        defaults.update({
            "role": "主材",
            "category_l1": "混凝土",
            "category_l2": "现浇混凝土材料",
            "unit": "m³",
            "waste_rate_estimate": 0.015,
            "loss_basis": "混凝土按浇筑体积采购，临时按运输、浇筑和现场损耗1.5%估算。",
            "coefficient": None,
            "supply": "乙供",
        })
    elif "灰土" in name or "素土" in name:
        defaults.update({
            "role": "垫层材料",
            "category_l1": "土石方及垫层材料",
            "category_l2": "灰土/回填土",
            "unit": "m³",
            "waste_rate_estimate": 0.03,
            "loss_basis": "灰土/素土按压实垫层体积估算，临时按拌合、摊铺和压实损耗3%估算。",
            "coefficient": None,
            "supply": "乙供",
        })
    elif "水泥" in name or "砂子" in name:
        defaults.update({
            "role": "面层辅材",
            "category_l1": "水泥砂浆材料",
            "category_l2": "水泥及砂",
            "unit": "kg",
            "waste_rate_estimate": 0.03,
            "loss_basis": "水泥砂子按现场撒布/拌制材料临时按3%估算。",
            "coefficient": None,
            "supply": "乙供",
        })
    elif "密封膏" in name or "嵌缝" in name:
        defaults.update({
            "role": "嵌缝材料",
            "category_l1": "密封材料",
            "category_l2": "嵌缝密封材料",
            "unit": "kg",
            "waste_rate_estimate": 0.05,
            "loss_basis": "密封膏按嵌缝施工残留、修边和损耗临时按5%估算。",
            "coefficient": None,
            "supply": "乙供",
        })
    elif any(k in name for k in ["多联式空调室外机", "多联机", "空调室外机", "风冷热泵机组", "空调机组"]):
        defaults.update({
            "role": "主材",
            "category_l1": "通风空调设备",
            "category_l2": "多联式空调设备",
            "unit": boq_unit or "台",
            "waste_rate_estimate": 0.0,
            "loss_basis": "成套设备按清单台数采购，不另计材料损耗。",
            "coefficient": 1.0,
            "supply": "乙供",
        })
    return defaults


def enrich_material_from_kb(item: dict, kb_context: dict) -> tuple[dict, list]:
    """
    AI 只负责材料候选与原因；分类、单位、损耗、系数、供应方式全部由知识库补齐或覆盖。
    """
    issues = []
    raw_name = str(item.get("material_name", "")).strip()
    if not raw_name:
        return item, ["空材料名称，无法知识库补齐。"]

    mapping_rule = find_material_rule(raw_name, kb_context["material_rules"])
    class_rule = match_kb_record(raw_name, kb_context["classification_rules"])
    loss_rule = match_kb_record(raw_name, kb_context["loss_rules"])

    if mapping_rule:
        item["material_name"] = mapping_rule["material_name"]
        item["mapping_rule_id"] = mapping_rule["id"]
        item["source"] = mapping_rule["id"]
    elif class_rule:
        item["material_name"] = class_rule["material_name"]
        item["mapping_rule_id"] = None
        item["source"] = "分类知识库"
    elif loss_rule:
        item["material_name"] = loss_rule["material_name"]
        item["mapping_rule_id"] = None
        item["source"] = "损耗知识库"
    else:
        item["material_name"] = raw_name
        item["mapping_rule_id"] = None
        if item.get("source_type") == "feature_explicit":
            item["source"] = item.get("source") or "feature_explicit"
            item["confidence"] = item.get("confidence") or "high"
        else:
            item["source"] = "AI推理-知识库未命中"
            item["confidence"] = "low"
            issues.append(f"{raw_name} 未命中材料映射库、分类库、损耗库。")

    fallback_defaults = infer_fallback_material_defaults(item.get("material_name", raw_name), kb_context.get("boq_item", {}))

    if class_rule:
        item["role"] = class_rule.get("role", "主材")
        item["category_l1"] = class_rule.get("category_l1", "")
        item["category_l2"] = class_rule.get("category_l2", "")
        item["category_l3"] = class_rule.get("category_l3", "")
        item["category_path"] = class_rule.get("category_path", "")
        item["display_path"] = class_rule.get("display_path", "")
        item["unit"] = class_rule.get("purchase_unit") or item.get("unit", "")
        # 如果损耗规则未命中但分类规则有损耗率，使用分类规则的损耗率
        if not loss_rule and class_rule.get("loss_rate") is not None:
            item["waste_rate_estimate"] = class_rule["loss_rate"]
            item["loss_basis"] = f"品类树标准损耗率，物料 {class_rule.get('material_id', '')}"
    elif mapping_rule:
        item["role"] = "未分类"
        item["category_l1"] = ""
        item["category_l2"] = ""
        item["category_l3"] = ""
        item["category_path"] = ""
        item["display_path"] = ""
        item["unit"] = mapping_rule.get("unit", item.get("unit", ""))
        issues.append(f"{item['material_name']} 未命中分类知识库，分类未确认。")
    else:
        # 兜底：对未命中的材料尝试在T3全量库中模糊查找
        direct_t3_match = _try_direct_t3_classification(raw_name)
        if direct_t3_match:
            item["role"] = direct_t3_match.get("role", "主材")
            item["category_l1"] = direct_t3_match.get("category_l1", "")
            item["category_l2"] = direct_t3_match.get("category_l2", "")
            item["category_l3"] = direct_t3_match.get("category_l3", "")
            item["category_path"] = direct_t3_match.get("category_path", "")
            item["display_path"] = direct_t3_match.get("display_path", "")
            item["unit"] = direct_t3_match.get("purchase_unit") or item.get("unit", "")
            if direct_t3_match.get("loss_rate") is not None:
                item["waste_rate_estimate"] = direct_t3_match["loss_rate"]
                item["loss_basis"] = f"品类树标准损耗率，物料 {direct_t3_match.get('material_id', '')}"
        else:
            item["role"] = item.get("role") or fallback_defaults["role"]
            item["category_l1"] = fallback_defaults["category_l1"]
            item["category_l2"] = fallback_defaults["category_l2"]
            item["category_l3"] = ""
            item["category_path"] = ""
            item["display_path"] = ""
            item["unit"] = item.get("unit") or fallback_defaults["unit"]
            if item.get("source_type") != "feature_explicit":
                issues.append(f"{item['material_name']} 分类未命中知识库，已按特征兜底为 {item['category_l1'] or '未分类'}。")

    if loss_rule:
        item["waste_rate_estimate"] = loss_rule["loss_rate"]
        item["loss_basis"] = loss_rule["loss_basis"]
    elif mapping_rule:
        item["waste_rate_estimate"] = fallback_defaults["waste_rate_estimate"]
        item["loss_basis"] = fallback_defaults["loss_basis"]
        issues.append(f"{item['material_name']} 未命中损耗知识库，损耗未确认。")
    else:
        item["waste_rate_estimate"] = fallback_defaults["waste_rate_estimate"]
        item["loss_basis"] = fallback_defaults["loss_basis"]
        issues.append(f"{item['material_name']} 损耗未命中知识库，已使用临时估算值。")

    if mapping_rule:
        item["coefficient"] = mapping_rule.get("coefficient")
        item["supply"] = mapping_rule.get("supply", item.get("supply", "不确定"))
    else:
        item["coefficient"] = fallback_defaults["coefficient"]
        item["supply"] = item.get("supply", fallback_defaults["supply"])

    if not item.get("reason") and mapping_rule:
        item["reason"] = mapping_rule.get("basis", "知识库规则命中")[:30]

    item, standard_issues = apply_t3_standardization(item, kb_context)
    if item.get("standardization_status") == "matched":
        # T3命中后，材料名称、单位、分类、损耗已经由标准物料库覆盖；
        # 保留真正需要复核的问题，避免把“旧规则库未命中”误报成材料不可用。
        issues = [
            msg for msg in issues
            if not any(flag in msg for flag in ["未命中材料映射库", "分类未命中知识库", "损耗未命中知识库"])
        ]
    issues.extend(standard_issues)

    return item, issues

def normalize_material_name(name: str, material_rules: list) -> str:
    name = str(name or "").strip()
    for rule in material_rules:
        names = [rule.get("material_name", "")] + rule.get("aliases", [])
        if any(alias and alias in name for alias in names):
            return rule.get("material_name", name)
    return name

def find_material_rule(material_name: str, material_rules: list):
    raw = compact_text(material_name)
    for rule in material_rules:
        if compact_text(rule.get("material_name", "")) == raw:
            return rule
    for rule in material_rules:
        aliases = [compact_text(x) for x in rule.get("aliases", []) if compact_text(x)]
        if raw in aliases:
            return rule
    best_rule, best_len = None, 0
    for rule in material_rules:
        names = [rule.get("material_name", "")] + rule.get("aliases", [])
        for alias in names:
            alias_norm = compact_text(alias)
            if len(alias_norm) < 2:
                continue
            if alias_norm in raw or raw in alias_norm:
                if len(alias_norm) > best_len:
                    best_rule, best_len = rule, len(alias_norm)
    return best_rule

def validate_and_constrain_materials(materials_obj: dict, kb_context: dict) -> tuple[dict, list]:
    material_rules = kb_context["material_rules"]
    forbidden = kb_context["forbidden_outputs"]
    boq_item = kb_context.get("boq_item") or globals().get("BOQ_ITEM", {})
    validated = []
    issues = []
    seen = set()

    for item in materials_obj.get("materials", []):
        if isinstance(item, str):
            item = {
                "material_name": item.strip(),
                "spec_hint": "",
                "reason": "模型返回字符串材料，系统转为材料对象。",
                "trigger_process": "模型文本抽取",
                "confidence": "medium",
            }
        elif not isinstance(item, dict):
            issues.append(f"存在非材料对象结果 {type(item).__name__}，已剔除。")
            continue

        raw_name = str(item.get("material_name", "")).strip()
        if not raw_name:
            issues.append("存在空材料名称，已剔除。")
            continue
        if any(word in raw_name for word in forbidden):
            issues.append(f"{raw_name} 属于禁止输出项，已剔除。")
            continue
        if item.get("source") == "project_default_kb":
            item["source_type"] = "project_default_kb"
            item.setdefault("requires_review_if_not_in_feature", True)
            item["confidence"] = "medium" if item.get("confidence") == "high" else item.get("confidence", "medium")

        item, preprocess_issues, invalid_name = preprocess_material_candidate(item, kb_context)
        issues.extend(preprocess_issues)
        if invalid_name:
            continue

        item, enrich_issues = enrich_material_from_kb(item, kb_context)
        issues.extend(enrich_issues)

        item, default_issues = attach_project_default_recommendation(item, kb_context)
        issues.extend(default_issues)

        if item.get("confidence") == "low" and not material_supported_by_current_context(item, kb_context):
            issues.append(f"{item.get('material_name')} 未被项目特征、默认推荐库或材料规则支撑，已剔除。")
            continue

        canonical_name = compact_text(item.get("material_name", ""))
        existing_item = next(
            (
                m for m in validated
                if compact_text(m.get("material_name", "")) == canonical_name
                or (item.get("rec_id") and m.get("rec_id") == item.get("rec_id"))
            ),
            None,
        )
        if existing_item:
            for field in [
                "rec_id", "project_default_system_id", "project_default_system_name",
                "project_default_typical_spec", "project_default_spec_params",
                "project_default_basis", "quantity_rule", "default_spec_values",
                "required_params", "loss_rate_hint", "unit_hint",
            ]:
                if item.get(field) and not existing_item.get(field):
                    existing_item[field] = item[field]
            if item.get("source_type") == "project_default_kb":
                existing_item.setdefault("requires_review_if_not_in_feature", item.get("requires_review_if_not_in_feature", True))
            if item.get("standard_refs") and not existing_item.get("standard_refs"):
                existing_item["standard_refs"] = item.get("standard_refs")
            issues.append(f"{item.get('material_name')} 重复输出，已合并默认库算量规则。")
            continue

        key = (item.get("material_name"), item.get("spec_hint", ""))
        if key in seen:
            issues.append(f"{item.get('material_name')} 重复输出，已去重。")
            continue
        seen.add(key)
        validated.append(item)

    existing = {m.get("material_name") for m in validated} | {m.get("t3_standard_name") for m in validated}
    for rule in material_rules:
        if rule.get("required") is True and rule.get("material_name") not in existing:
            item = {
                "material_name": rule["material_name"],
                "spec_hint": boq_item.get("concrete_strength", "") if "混凝土" in rule["material_name"] else "",
                "confidence": "high",
                "reason": rule.get("basis", "知识库必需材料")[:30],
                "trigger_process": "知识库required补入",
            }
            item, preprocess_issues, invalid_name = preprocess_material_candidate(item, kb_context)
            issues.extend(preprocess_issues)
            if invalid_name:
                continue
            item, enrich_issues = enrich_material_from_kb(item, kb_context)
            issues.append(f"required材料缺失：{rule['material_name']}，已按知识库补入。")
            issues.extend(enrich_issues)
            key = (item.get("material_name"), item.get("spec_hint", ""))
            if key in seen:
                issues.append(f"{item.get('material_name')} 重复输出，已去重。")
                continue
            seen.add(key)
            validated.append(item)

    return {"materials": validated}, issues


def infer_materials_by_rules(processes_obj: dict, kb_context: dict) -> dict:
    """当关闭 AI 材料推导时，只允许基于已命中的材料映射知识库生成候选材料。"""
    materials = []
    for rule in kb_context.get("material_rules", []):
        if rule.get("required") is True:
            materials.append(
                {
                    "material_name": rule.get("material_name", ""),
                    "spec_hint": "",
                    "reason": rule.get("basis", "知识库required材料")[:40],
                    "trigger_process": "知识库规则",
                    "confidence": "high",
                }
            )
    return {"materials": materials}


def parse_length_to_m(value: str, unit: str) -> float | None:
    try:
        num = float(value)
    except Exception:
        return None
    unit = (unit or "").lower()
    if unit == "mm":
        return num / 1000
    if unit == "cm":
        return num / 100
    if unit == "m":
        return num
    return None


def material_core_tokens(name: str) -> list[str]:
    name = compact_text(name).replace("：", ":")
    tokens = []
    for token in re.findall(r"C\d+|\d+[:：]\d+|[\u4e00-\u9fffA-Za-z]+", name):
        if len(token) >= 2 and token not in {"材料", "面层", "垫层"}:
            tokens.append(token)
    return tokens or [name]


def find_thickness_m_for_material(material_name: str, feature_text: str) -> float | None:
    tokens = material_core_tokens(material_name)
    lines = split_numbered_items(feature_text) or [feature_text]
    for line in lines:
        compact_line = compact_text(line).replace("：", ":")
        if any(t and t in compact_line for t in tokens):
            m = re.search(r"(\d+(?:\.\d+)?)\s*(mm|cm|m)?\s*厚", line, re.I)
            if m:
                return parse_length_to_m(m.group(1), m.group(2) or "mm")
    return None


def parse_first_number(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]
    if not nums:
        return None
    # 规格范围如 60-80mm 取中值，作为默认推荐参数，并在 Stage6 标记复核。
    if len(nums) >= 2 and re.search(r"\d+(?:\.\d+)?\s*[-~～至]\s*\d+(?:\.\d+)?", text):
        return sum(nums[:2]) / 2
    return nums[0]


def feature_lines_for_material(material: dict, feature_text: str) -> list[str]:
    lines = split_numbered_items(feature_text) or str(feature_text or "").splitlines() or [str(feature_text or "")]
    tokens = set()
    for name in [
        material.get("material_name", ""),
        material.get("raw_material_name", ""),
        material.get("input_material_name", ""),
        material.get("_name_before_spec_split", ""),
        material.get("t3_standard_name", ""),
        material.get("project_default_typical_spec", ""),
    ]:
        tokens.update(material_core_tokens(name))
    name_text = compact_text(" ".join(tokens))
    if "挤塑" in name_text or "xps" in name_text.lower():
        tokens.update(["挤塑", "XPS", "保温板", "保温层"])
    if "岩棉" in name_text:
        tokens.update(["岩棉", "保温板", "保温层"])
    if "砂浆" in name_text:
        tokens.update(["砂浆", "抹面", "抗裂"])
    if "网格布" in name_text or "玻纤" in name_text:
        tokens.update(["网格布", "玻纤"])
    if "粘接" in name_text or "粘结" in name_text:
        tokens.update(["粘接剂", "粘结剂", "胶粘剂"])
    compact_tokens = [compact_text(t) for t in tokens if compact_text(t)]
    matched = [line for line in lines if any(t in compact_text(line) for t in compact_tokens)]
    return matched or lines


def extract_thickness_mm_from_feature(boq_item: dict, material: dict) -> tuple[float | None, str]:
    feature_text = boq_item.get("feature_text", "")
    for line in feature_lines_for_material(material, feature_text):
        for pattern in [
            r"(?:厚度|材料厚度)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(mm|cm|m)?",
            r"(\d+(?:\.\d+)?)\s*(mm|cm|m)?\s*厚",
            r"(\d+(?:\.\d+)?)\s*厚",
        ]:
            m = re.search(pattern, line, re.I)
            if not m:
                continue
            unit = m.group(2) if len(m.groups()) >= 2 and m.group(2) else "mm"
            metres = parse_length_to_m(m.group(1), unit)
            if metres is not None:
                return metres * 1000, f"feature:{line.strip()[:60]}"
    return None, ""


def extract_layer_count_from_feature(boq_item: dict, material: dict) -> tuple[float | None, str]:
    cn_num = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5}
    for line in feature_lines_for_material(material, boq_item.get("feature_text", "")):
        m = re.search(r"([一二两三四五]|\d+)\s*层", line)
        if m:
            raw = m.group(1)
            return float(cn_num.get(raw, raw)), f"feature:{line.strip()[:60]}"
    return None, ""


def extract_fire_rating_from_feature(boq_item: dict) -> tuple[str | None, str]:
    text = f"{boq_item.get('name', '')} {boq_item.get('standard_name', '')} {boq_item.get('feature_text', '')}"
    m = re.search(r"[AB]\s*[12]级|不燃\s*A级|阻燃\s*B1", text, re.I)
    if not m:
        return None, ""
    return compact_text(m.group(0)), "feature"


def get_project_default_loss(material: dict) -> tuple[float | None, str]:
    for value, source in [
        (material.get("waste_rate_estimate"), material.get("loss_basis", "材料损耗规则")),
        (material.get("loss_rate_hint"), "project_defaults_kb.loss_rate_hint"),
    ]:
        if value is None or value == "":
            continue
        try:
            loss = float(value)
            if loss > 1:
                loss = loss / 100
            return loss, source
        except Exception:
            continue
    return None, ""


def resolve_project_default_params(boq_item: dict, material: dict) -> dict:
    rule = material.get("quantity_rule") or {}
    defaults = material.get("default_spec_values") or {}
    params, sources, used_defaults = {}, {}, []

    loss, loss_source = get_project_default_loss(material)
    if loss is not None:
        params["loss_rate"] = loss
        params["LOSS"] = loss
        sources["loss_rate"] = loss_source

    thickness_mm, thickness_source = extract_thickness_mm_from_feature(boq_item, material)
    if thickness_mm is not None:
        params["thickness_mm"] = thickness_mm
        params["thickness"] = thickness_mm
        sources["thickness_mm"] = thickness_source
    else:
        for key in ["thickness_mm", "thickness"]:
            value = defaults.get(key)
            if value is None:
                value = rule.get(f"default_{key}")
            parsed = parse_first_number(value)
            if parsed is not None:
                params["thickness_mm"] = parsed
                params["thickness"] = parsed
                sources["thickness_mm"] = f"default:{key}"
                used_defaults.append("thickness_mm")
                break

    fire_rating, fire_source = extract_fire_rating_from_feature(boq_item)
    if fire_rating:
        params["fire_rating"] = fire_rating
        sources["fire_rating"] = fire_source
    elif defaults.get("fire_rating"):
        params["fire_rating"] = defaults.get("fire_rating")
        sources["fire_rating"] = "default:fire_rating"
        used_defaults.append("fire_rating")

    layer_count, layer_source = extract_layer_count_from_feature(boq_item, material)
    if layer_count is not None:
        params["layer_count"] = layer_count
        sources["layer_count"] = layer_source

    for key in [
        "coeff", "density", "coverage", "coats", "usage_per_m", "usage_per_m2",
        "usage_per_ton", "turnover", "ratio", "unit_weight", "density_per_m2",
        "dft", "solids", "rebound_coeff", "blocks_per_m3", "cross_section",
        "strand_count",
    ]:
        default_key = f"default_{key}"
        if default_key in rule and key not in params:
            parsed = parse_first_number(rule.get(default_key))
            if parsed is not None:
                params[key] = parsed
                sources[key] = f"quantity_rule.{default_key}"
                used_defaults.append(key)

    aliases = {
        "coverage_rate": "coverage",
        "turnover_times": "turnover",
        "rebound_rate": "rebound_coeff",
    }
    for alias, canonical in aliases.items():
        if canonical in params and alias not in params:
            params[alias] = params[canonical]
            sources[alias] = sources.get(canonical, "")

    required = material.get("required_params") or []
    missing_required = [
        p for p in required
        if p not in params and p not in defaults and f"default_{p}" not in rule
    ]
    return {
        "params": params,
        "param_sources": sources,
        "used_default_params": sorted(set(used_defaults)),
        "missing_required_params": missing_required,
    }


def calculate_by_project_default_rule(boq_item: dict, material: dict) -> dict | None:
    rule = material.get("quantity_rule") or {}
    if not rule:
        return None

    name = material.get("material_name", "")
    try:
        boq_qty = float(boq_item.get("quantity") or 0)
    except Exception:
        boq_qty = 0
    if boq_qty <= 0:
        return None

    boq_unit = normalize_unit(boq_item.get("unit", ""))
    formula = str(rule.get("formula", ""))
    formula_upper = formula.upper()
    resolved = resolve_project_default_params(boq_item, material)
    params = resolved["params"]
    missing = []

    def need_base(var_name: str, allowed_units: set[str]) -> float | None:
        if var_name not in formula_upper:
            return None
        if boq_unit in allowed_units or not boq_unit:
            return boq_qty
        missing.append(f"{var_name} 需要清单单位为 {','.join(sorted(allowed_units))}，当前为 {boq_item.get('unit', '')}")
        return None

    area = need_base("BOQ_AREA", {"m²"})
    volume = need_base("BOQ_VOLUME", {"m³"})
    length = need_base("BOQ_LENGTH", {"m"})
    count = need_base("BOQ_COUNT", {"个", "套", "樘", "扇", "座", "项"})
    qty = boq_qty

    design_qty = None
    coeff = params.get("coeff")
    output_unit = material.get("unit") or material.get("unit_hint") or ""

    # 常见面积类工程：保温板、砂浆、涂料、网格布、界面剂等。
    if "BOQ_AREA" in formula_upper and area is not None:
        if "THICKNESS" in formula_upper and ("DENSITY" in formula_upper):
            thickness_mm = params.get("thickness_mm")
            density = params.get("density")
            if thickness_mm is None:
                missing.append("缺少 thickness_mm")
            if density is None:
                missing.append("缺少 density")
            if not missing:
                design_qty = area * thickness_mm * density
                # 0.0018 这类密度表示 t/m²/mm；采购单位为 kg 时换算为 kg。
                if normalize_unit(output_unit) == "kg" and density < 0.1:
                    design_qty *= 1000
        elif "THICKNESS" in formula_upper:
            thickness_mm = params.get("thickness_mm")
            if thickness_mm is None:
                missing.append("缺少 thickness_mm")
            else:
                design_qty = area * thickness_mm / 1000
                output_unit = output_unit or "m³"
        elif "USAGE_PER_M2" in formula_upper:
            usage = params.get("usage_per_m2")
            if usage is None:
                missing.append("缺少 usage_per_m2")
            else:
                design_qty = area * usage
        elif "COVERAGE" in formula_upper:
            coverage = params.get("coverage")
            coats = params.get("coats", 1)
            if coverage is None:
                missing.append("缺少 coverage")
            else:
                design_qty = area / coverage * coats
        elif "COEFF" in formula_upper or "BOQ_AREA" in formula_upper:
            coeff = params.get("coeff", 1.0)
            if "网格布" in compact_text(name) and params.get("layer_count"):
                coeff *= params["layer_count"]
            design_qty = area * coeff

    elif "BOQ_VOLUME" in formula_upper and volume is not None:
        if "BLOCKS_PER_M3" in formula_upper:
            blocks = params.get("blocks_per_m3") or params.get("coeff")
            if blocks is None:
                missing.append("缺少 blocks_per_m3")
            else:
                design_qty = volume * blocks
        elif "REBOUND" in formula_upper:
            design_qty = volume * params.get("rebound_coeff", params.get("coeff", 1.0))
        else:
            design_qty = volume * params.get("coeff", 1.0)

    elif "BOQ_LENGTH" in formula_upper and length is not None:
        if "USAGE_PER_M" in formula_upper:
            usage = params.get("usage_per_m")
            if usage is None:
                missing.append("缺少 usage_per_m")
            else:
                design_qty = length * usage
        else:
            design_qty = length * params.get("coeff", 1.0)

    elif "BOQ_COUNT" in formula_upper and count is not None:
        if "BOQ_LENGTH" in formula_upper and params.get("unit_weight"):
            design_qty = count * params["unit_weight"]
        else:
            design_qty = count * params.get("coeff", 1.0)

    elif "BOQ_QTY" in formula_upper:
        design_qty = qty * params.get("coeff", 1.0)

    elif "STEEL_QTY" in formula_upper or "STEEL_TON" in formula_upper:
        if boq_unit != "t":
            missing.append(f"STEEL_QTY 需要清单单位为 t，当前为 {boq_item.get('unit', '')}")
        elif "USAGE_PER_TON" in formula_upper:
            usage = params.get("usage_per_ton")
            if usage is None:
                missing.append("缺少 usage_per_ton")
            else:
                design_qty = qty * usage
        elif "RATIO" in formula_upper:
            ratio = params.get("ratio")
            if ratio is None:
                missing.append("缺少 ratio")
            else:
                design_qty = qty * ratio
        else:
            design_qty = qty * params.get("coeff", 1.0)

    elif "CRACK_LENGTH" in formula_upper:
        if boq_unit != "m":
            missing.append(f"CRACK_LENGTH 需要清单单位为 m，当前为 {boq_item.get('unit', '')}")
        else:
            cross_section = params.get("cross_section")
            density = params.get("density")
            if cross_section is None:
                missing.append("缺少 cross_section")
            if density is None:
                missing.append("缺少 density")
            if not missing:
                design_qty = qty * cross_section * density

    elif "STEEL_AREA" in formula_upper:
        if boq_unit != "m²":
            missing.append(f"STEEL_AREA 需要清单单位为 m²，当前为 {boq_item.get('unit', '')}")
        else:
            dft = params.get("dft")
            density = params.get("density")
            solids = params.get("solids")
            if None in (dft, density, solids):
                missing.append("缺少 dft/density/solids")
            else:
                design_qty = qty * dft * density / solids / 1000

    elif units_compatible(material.get("unit", ""), boq_item.get("unit", "")):
        design_qty = qty * params.get("coeff", 1.0)

    if design_qty is None:
        missing.append("项目默认推荐规则参数不足，无法执行 quantity_rule")

    loss = params.get("loss_rate")
    if loss is None:
        missing.append("缺少 loss_rate")

    missing_required = resolved["missing_required_params"]
    review_reasons = []
    if resolved["used_default_params"]:
        review_reasons.append("使用项目默认参数")
    if missing_required:
        review_reasons.append("缺少必填规格参数: " + ",".join(missing_required))

    if design_qty is None or loss is None:
        return {
            "material_name": name,
            "rec_id": material.get("rec_id", ""),
            "coefficient": coeff,
            "quantity": None,
            "design_qty": round(design_qty, 3) if design_qty is not None else None,
            "loss_qty": None,
            "procurement_qty": None,
            "unit": output_unit,
            "loss_rate": loss,
            "formula": rule.get("formula_desc") or formula,
            "quantity_rule_formula": formula,
            "calculation_source": "project_defaults_kb.quantity_rule",
            "needs_review": True,
            "missing_params": list(dict.fromkeys(missing + missing_required)),
            "missing_loss_rate": loss is None,
            "used_default_params": resolved["used_default_params"],
            "param_sources": resolved["param_sources"],
            "notes": "；".join(list(dict.fromkeys(missing + review_reasons))),
        }

    loss_qty = design_qty * loss
    procurement_qty = design_qty + loss_qty
    return {
        "material_name": name,
        "rec_id": material.get("rec_id", ""),
        "coefficient": round(float(coeff), 6) if coeff is not None else None,
        "quantity": round(procurement_qty, 3),
        "design_qty": round(design_qty, 3),
        "loss_qty": round(loss_qty, 3),
        "procurement_qty": round(procurement_qty, 3),
        "unit": output_unit,
        "loss_rate": loss,
        "formula": rule.get("formula_desc") or formula,
        "quantity_rule_formula": formula,
        "calculation_source": "project_defaults_kb.quantity_rule",
        "needs_review": bool(review_reasons),
        "missing_params": missing_required,
        "missing_loss_rate": False,
        "used_default_params": resolved["used_default_params"],
        "param_sources": resolved["param_sources"],
        "notes": "；".join(review_reasons),
    }


def calculate_material_quantity(boq_item: dict, material: dict, kb_context: dict) -> dict:
    """Stage 3: 纯数学用量计算。
    损耗率优先级固定：T2规则/知识库 > 品类树standard_loss_rate > pending。
    参数不足时不估算、不脑补，直接标记 missing_params + needs_review。
    """
    name = material.get("material_name", "")
    boq_qty = float(boq_item.get("quantity") or 0)
    if boq_qty <= 0:
        return {
            "material_name": name,
            "coefficient": None,
            "quantity": None,
            "design_qty": None,
            "loss_qty": None,
            "procurement_qty": None,
            "unit": material.get("unit", ""),
            "loss_rate": None,
            "formula": "",
            "needs_review": True,
            "missing_params": ["工程量缺失或为零，无法计算"],
            "missing_loss_rate": False,
            "notes": "工程量缺失，所有计算 pending",
        }

    project_default_calc = calculate_by_project_default_rule(boq_item, material)
    if project_default_calc is not None:
        return project_default_calc

    boq_unit = normalize_unit(boq_item.get("unit", ""))
    mat_unit = normalize_unit(material.get("unit", ""))
    loss = material.get("waste_rate_estimate")
    missing_loss_rate = loss is None
    if loss is None:
        loss = None
    else:
        try:
            loss = float(loss)
        except (ValueError, TypeError):
            missing_loss_rate = True
            loss = None
    notes = []
    missing = []

    design_qty = None
    coeff = material.get("coefficient")
    formula = ""

    if mat_unit == "m³" and boq_unit == "m²":
        thickness = None
        for match_name in [material.get("raw_material_name", ""), name]:
            if not match_name:
                continue
            thickness = find_thickness_m_for_material(match_name, boq_item.get("feature_text", ""))
            if thickness is not None:
                break
        if thickness is not None:
            design_qty = boq_qty * thickness
            coeff = thickness
            formula = f"面积({boq_qty}) x 厚度({thickness}m)"
        else:
            missing.append("缺少可识别厚度，无法由面积换算体积")
    elif mat_unit == "m²" and boq_unit == "m²":
        try:
            coeff = float(coeff) if coeff is not None else 1.0
        except Exception:
            coeff = 1.0
        design_qty = boq_qty * coeff
        formula = f"面积({boq_qty}) x 系数({coeff})"
    elif coeff is not None:
        try:
            coeff = float(coeff)
            design_qty = boq_qty * coeff
            formula = f"清单量({boq_qty}) x 系数({coeff})"
        except Exception:
            missing.append(f"系数不可解析: {coeff}")
    elif mat_unit and units_compatible(mat_unit, boq_unit):
        design_qty = boq_qty
        coeff = 1.0
        formula = f"同单位清单量({boq_qty})"
    else:
        missing.append("缺少消耗系数或换算参数，不能准确计算采购量")

    if design_qty is None:
        return {
            "material_name": name,
            "coefficient": coeff,
            "quantity": None,
            "design_qty": None,
            "loss_qty": None,
            "procurement_qty": None,
            "unit": material.get("unit", ""),
            "loss_rate": loss if not missing_loss_rate else None,
            "formula": formula,
            "needs_review": True,
            "missing_params": missing,
            "missing_loss_rate": missing_loss_rate,
            "notes": "；".join(missing + (["损耗率缺失，标记为 pending"] if missing_loss_rate else [])),
        }

    if missing_loss_rate:
        missing.append("损耗率无依据（非T2规则、非品类树），采购量不估算，标记 pending")
        return {
            "material_name": name,
            "coefficient": round(float(coeff), 6) if coeff is not None else None,
            "quantity": None,
            "design_qty": round(design_qty, 3),
            "loss_qty": None,
            "procurement_qty": None,
            "unit": material.get("unit", ""),
            "loss_rate": None,
            "formula": formula,
            "needs_review": True,
            "missing_params": missing,
            "missing_loss_rate": True,
            "notes": "；".join(missing),
        }

    loss_qty = design_qty * loss
    procurement_qty = design_qty + loss_qty
    return {
        "material_name": name,
        "coefficient": round(float(coeff), 6) if coeff is not None else None,
        "quantity": round(procurement_qty, 3),
        "design_qty": round(design_qty, 3),
        "loss_qty": round(loss_qty, 3),
        "procurement_qty": round(procurement_qty, 3),
        "unit": material.get("unit", ""),
        "loss_rate": loss if not missing_loss_rate else None,
        "formula": formula,
        "needs_review": bool(missing) or missing_loss_rate,
        "missing_params": missing,
        "missing_loss_rate": missing_loss_rate,
        "notes": "；".join(missing),
    }
def build_accuracy_review(boq_item: dict, feature_materials: dict, materials: dict, calculations: list[dict], strategy: PromptPlan) -> dict:
    material_names = {
        compact_text(name)
        for m in materials.get("materials", [])
        for name in [m.get("material_name", ""), m.get("raw_material_name", "")]
        if name
    }
    explicit_names = [
        m.get("material_name", "")
        for m in feature_materials.get("materials", [])
        if m.get("material_name") and compact_text(m.get("material_name", "")) not in {"其他", "详见", "待定"}
    ]
    missing_explicit = [
        name for name in explicit_names
        if compact_text(name) not in material_names
        and not any(compact_text(name) in existing or existing in compact_text(name) for existing in material_names)
    ]
    q0_confidence = boq_item.get("q0_match", {}).get("confidence", "unknown")
    qty_missing = [
        {"material_name": c.get("material_name", ""), "missing_params": c.get("missing_params", [])}
        for c in calculations
        if c.get("needs_review") and c.get("missing_params")
    ]
    default_param_review = [
        c.get("material_name", "")
        for c in calculations
        if c.get("needs_review") and c.get("used_default_params") and not c.get("missing_params")
    ]
    unsupported_ai = [
        m.get("material_name", "")
        for m in materials.get("materials", [])
        if m.get("source") == "AI推理-知识库未命中"
        and compact_text(m.get("material_name", "")) not in {compact_text(x) for x in explicit_names}
    ]
    unstandardized = [
        m.get("material_name", "")
        for m in materials.get("materials", [])
        if m.get("standardization_status") != "matched"
    ]
    critical_unstandardized = [
        m.get("material_name", "")
        for m in materials.get("materials", [])
        if m.get("standardization_status") not in ("matched", "feature_explicit")
        and m.get("source_type") not in ("project_default_kb", "feature_explicit")
    ]
    standards_missing = [
        m.get("material_name", "")
        for m in materials.get("materials", [])
        if m.get("standardization_status") == "matched"
        and not format_standard_refs(m.get("standard_refs", []), m.get("standard_code", ""))
        and not m.get("project_default_basis")
    ]
    name_review = [
        m.get("material_name", "")
        for m in materials.get("materials", [])
        if m.get("needs_name_review") or m.get("needs_standard_review")
    ]
    project_default_review = [
        m.get("material_name", "")
        for m in materials.get("materials", [])
        if m.get("source_type") == "project_default_kb"
        and m.get("requires_review_if_not_in_feature", True)
        and compact_text(m.get("material_name", "")) not in {compact_text(x) for x in explicit_names}
    ]
    # 新增：编码名称冲突检查
    code_name_conflict = boq_item.get("code_name_conflict")
    # 新增：禁止材料混入检查
    forbidden_names = set(compact_text(f) for f in (strategy.forbidden_materials if hasattr(strategy, 'forbidden_materials') else []))
    forbidden_found = [
        m.get("material_name", "")
        for m in materials.get("materials", [])
        if compact_text(m.get("material_name", "")) in forbidden_names
    ]
    # 新增：损失率缺失检查
    missing_loss_rate = [
        m.get("material_name", "")
        for m in materials.get("materials", [])
        if m.get("waste_rate_estimate") is None and m.get("role") == "主材"
    ]
    severe = []
    if missing_explicit:
        severe.append("显性材料被遗漏")
    if q0_confidence == "low":
        severe.append("Q0标准清单低置信命中")
    if q0_confidence == "none":
        severe.append("Q0标准清单未命中，无法验证编码正确性")
    if code_name_conflict:
        severe.append(f"编码名称冲突: {code_name_conflict.get('message', '')}")
    if not materials.get("materials"):
        severe.append("材料清单为空")
    if unsupported_ai:
        severe.append("存在未被标准库支撑的AI材料")
    if critical_unstandardized:
        severe.append("存在未通过标准物料校验的材料")
    if forbidden_found:
        severe.append("存在被PromptPlan禁止的材料混入")
    if missing_loss_rate:
        severe.append("存在主材缺少损耗率依据")
    return {
        "missingExplicitMaterials": missing_explicit,
        "quantityParamsMissing": qty_missing,
        "defaultParamReviewMaterials": default_param_review,
        "standardMatchRisk": q0_confidence,
        "codeNameConflict": code_name_conflict,
        "unsupportedAiOnlyMaterials": unsupported_ai,
        "unstandardizedMaterials": unstandardized,
        "standardsMissing": standards_missing,
        "nameOrStandardReviewMaterials": name_review,
        "projectDefaultReviewMaterials": project_default_review,
        "forbiddenMaterialsFound": forbidden_found,
        "missingLossRateMaterials": missing_loss_rate,
        "strategyRiskFlags": strategy.risk_flags if hasattr(strategy, 'risk_flags') else [],
        "needsHumanReview": bool(
            missing_explicit or qty_missing or default_param_review or q0_confidence in ("low", "none") or unsupported_ai
            or critical_unstandardized or standards_missing or name_review
            or project_default_review
            or (strategy.risk_flags if hasattr(strategy, 'risk_flags') else [])
            or code_name_conflict or forbidden_found or missing_loss_rate
        ),
        "severeIssues": severe,
    }
def should_use_ai_for_process(kb_context: dict) -> bool:
    return any(r.get("id") == "GENERIC-PROCESS" for r in kb_context.get("process_rules", []))

def should_use_ai_for_materials(kb_context: dict) -> bool:
    return not bool(kb_context.get("material_rules"))


def build_fallback_strategy(boq_item: dict, kb_context: dict, feature_materials: dict) -> PromptPlan:
    """确定性兜底策略，生成基础 PromptPlan。不做 AI 推理，不做硬编码默认值。"""
    explicit = [m.get("material_name", "") for m in feature_materials.get("materials", []) if m.get("material_name")]
    standard_name = boq_item.get("standard_name", "") or boq_item.get("name", "")
    # 将清单项目名称作为主设备/主材纳入 must_include，避免主材被遗漏
    boq_name = boq_item.get("name", "").strip()
    if boq_name and boq_name not in explicit and not re.search(r'(安装|施工|运输|搬运|清理|调试)$', boq_name):
        explicit.append(boq_name)
    q0_match = boq_item.get("q0_match", {})
    q0_conf = q0_match.get("confidence", "unknown")

    forbidden = []
    risk_flags = []
    if q0_conf == "none":
        risk_flags.append("Q0 标准清单未命中，所有推理基于项目特征和通用知识，准确度受限。")
    elif q0_conf == "low":
        risk_flags.append("Q0 标准清单低置信命中，需要人工复核标准范围。")

    code_conflict = boq_item.get("code_name_conflict")
    if code_conflict:
        risk_flags.append(f"编码名称冲突: {code_conflict.get('message', '')}")

    # 暖通空调设备的风机约束：整体式室外机内置风机属设备本体安装，不得拆为独立风机材料
    t1 = boq_item.get("t1_section", "") or ""
    name = boq_item.get("name", "") or ""
    if "通风" in t1 or "空调" in name:
        risk_flags.append(
            "整体式空调室外机（多联机/风冷热泵/VRF/VRV）的「风机安装」指设备内置风机，"
            "属于设备本体安装工序的一部分，不得单独拆解为独立风机采购材料。"
            "工序拆解不得因此新增独立风机设备条目。"
        )
        forbidden.extend(["风机", "风机(轴流/离心/混流)", "轴流风机", "离心风机", "混流风机"])

    return PromptPlan(
        stage1_prompt="",
        stage2_prompt="",
        stage5_prompt="",
        quantity_strategy={
            "area_to_volume": "面积清单中带厚度的体积类材料按 面积×厚度 计算。",
            "incompatible_units": "kg、m、套等无法由清单量直接推出的材料标记为 pending。",
        },
        forbidden_materials=forbidden,
        risk_flags=risk_flags,
        must_include_materials=explicit,
    )
def run_meta_strategy(boq_item: dict, kb_context: dict, feature_comparison: dict, feature_materials: dict, L1: dict, L2: dict) -> PromptPlan:
    """Stage0: 生成 PromptPlan。AI 生成核心内容，代码注入安全外壳。"""
    fallback = build_fallback_strategy(boq_item, kb_context, feature_materials)
    if not RUN_META_STRATEGY:
        return fallback

    prompt_payload = {
        "L1_project": L1,
        "L2_item": L2,
        "explicit_materials": fallback.must_include_materials,
        "constraints": [
            "准确度优先，不能为了完整性编造材料。",
            "输入项目特征明示材料必须进入 must_include_materials。",
            "若输入明确指定材料类型（如钢制保温门），must_include_materials 中必须保留原始材料名，"
            "不得替换为 T3 近似条目（如冷藏库门、铝合金门）。",
            "Q0 命中低置信时，只能说明候选，不得强行按错误标准项推理。",
            "Q1/Q2 定额只能作为候选知识，实际特征冲突时以实际特征为准。",
            "人工、机械、服务费不得进入采购材料。",
            "project_defaults_kb 只作缺参数推荐锚点；不得把默认推荐当成最终采购结论。"
            "若默认推荐的材料类型与输入特征明示不一致（如输入钢制保温门但默认推荐铝合金门），"
            "必须标记为 knowledge_conflicts 并拒绝采用该推荐。",
            "采纳 project_defaults_kb 推荐时必须标记 source_type=project_default_kb 且 confidence 不得高于 medium。",
            "T3 标准物料库是标准化参考，不是硬性约束——若输入材料不在 T3 库中，保留原始名称，"
            "在 stage2_prompt 中明确要求不得替换为近似 T3 条目。",
        ],
    }
    try:
        raw = call_ai(
            "Stage 0 Meta PromptPlan",
            [
                {
                    "role": "system",
                    "content": (
                        "你是中国建筑工程量清单拆解策略专家。你的任务不是直接输出采购清单，"
                        "而是基于输入清单、Q0国标、Q1/Q2定额候选和显性材料，生成各阶段的专用提示词计划(PromptPlan)。"
                        "必须保守、可审计；只输出JSON。"
                        "核心原则：材料类型以输入项目特征的明示内容为准，T3 标准库是标准化参考而非硬性替代依据。"
                        "若输入明确指定材料类型（如钢制保温门），在 stage1/2_prompt 中必须写入指令："
                        "不得将此材料替换为 T3 标准库中的近似条目（如冷藏库门、铝合金门）。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"上下文:{json.dumps(prompt_payload, ensure_ascii=False, separators=(',', ':'))}\n"
                        "请输出JSON格式：\n"
                        '{"stage1_prompt":"给工序拆解的定制提示词",'
                        '"stage2_prompt":"给材料推导的定制提示词",'
                        '"stage5_prompt":"给审核阶段的定制提示词",'
                        '"quantity_strategy":{"area_to_volume":"策略","incompatible_units":"策略"},'
                        '"forbidden_materials":["禁止出现的材料名"],'
                        '"risk_flags":["风险标记"],'
                        '"knowledge_conflicts":[{"desc":"冲突描述"}],'
                        '"must_include_materials":["必须包含的材料名"]}'
                    ),
                },
            ],
            max_tokens=3000,
        )
        parsed = ensure_object_with_key(extract_json(raw), "strategy")
        if "strategy" in parsed and isinstance(parsed["strategy"], dict):
            parsed = parsed["strategy"]

        # 将 AI 输出合并到 PromptPlan，fallback 兜底空字段
        plan = PromptPlan(
            stage1_prompt=parsed.get("stage1_prompt", ""),
            stage2_prompt=parsed.get("stage2_prompt", ""),
            stage5_prompt=parsed.get("stage5_prompt", ""),
            quantity_strategy=parsed.get("quantity_strategy", fallback.quantity_strategy),
            forbidden_materials=list(dict.fromkeys(
                (parsed.get("forbidden_materials") or []) + fallback.forbidden_materials
            )),
            risk_flags=list(dict.fromkeys(
                (parsed.get("risk_flags") or []) + fallback.risk_flags
            )),
            knowledge_conflicts=parsed.get("knowledge_conflicts", []) or [],
            must_include_materials=list(dict.fromkeys(
                (parsed.get("must_include_materials") or []) + fallback.must_include_materials
            )),
        )
        return plan
    except Exception as e:
        print(f"Stage 0 Meta策略失败，使用确定性兜底策略: {e}")
        return fallback
def materials_from_strategy(strategy: PromptPlan) -> dict:
    """从 PromptPlan 中提取必须纳入的材料（Stage0 强约束）。"""
    rows = []
    for name in strategy.must_include_materials:
        if not name:
            continue
        rows.append({
            "material_name": name,
            "reason": "PromptPlan 强制纳入，来自输入显性材料或强约束",
            "trigger_process": "Stage0 PromptPlan",
            "evidence": "must_include_materials",
            "confidence": "medium",
            "applicability": "适用",
        })
    return {"materials": rows}

def derive_display_spec(m: dict) -> str:
    """从材料对象中综合推导展示用规格型号。

    优先级: typical_spec（完整描述，最优先展示）> spec_hint > extracted_specs > spec_params
    去重逻辑: 若某个参数值已被 typical_spec 包含，不再重复列出。
    """
    ts = (m.get("project_default_typical_spec") or m.get("typical_spec") or "").strip()
    if ts and ts in ("string", "待定"):
        ts = ""

    parts = []
    # 1. 默认库的完整规格描述（最优先）
    if ts:
        parts.append(ts)

    def _contained(val: str) -> bool:
        """检查 val 是否已被已有 parts 中的文本包含。"""
        return any(val in p or p in val for p in parts)

    # 2. AI或名称拆分出的规格提示
    sh = m.get("spec_hint", "").strip()
    if sh and sh not in ("无", "规格线索", "待定") and not _contained(sh):
        parts.append(sh)

    # 3. 结构化提取的规格参数
    for es in m.get("extracted_specs", []) or []:
        v = es.get("value", "").strip()
        if v and not _contained(v):
            parts.append(v)

    # 4. 规格参数字典中未被包含的关键值
    sp = m.get("spec_params") or m.get("project_default_spec_params") or {}
    if isinstance(sp, dict):
        for v in sp.values():
            if v and str(v) not in ("待定",) and not _contained(str(v)):
                parts.append(str(v))

    # 5. T3默认规格值中未被包含的关键值
    dv = m.get("default_spec_values") or {}
    if isinstance(dv, dict):
        skip_keys = {"spec_text", "pump_method", "method"}
        for k, v in dv.items():
            if k in skip_keys:
                continue
            if v and str(v) not in ("待定",) and not _contained(str(v)):
                parts.append(str(v))

    return "；".join(parts) if parts else "—"


def process_boq_item(boq_item: dict, index: int, L1_override: dict | None = None) -> dict:
    global BOQ_ITEM
    BOQ_ITEM = boq_item

    print("\n" + "=" * 80)
    print(
        f"处理清单 {index}: {boq_item.get('code', '')} "
        f"{boq_item.get('name', '')} {boq_item.get('feature_text', '')} "
        f"{boq_item.get('quantity', '')}{boq_item.get('unit', '')}"
    )

    kb_context = retrieve_kb_context(boq_item)
    feature_comparison = build_feature_comparison(boq_item, kb_context)
    feature_materials = build_materials_from_feature_hints(boq_item)
    L1 = L1_override if L1_override is not None else build_L1_context()
    project_defaults = retrieve_project_defaults(boq_item, L1)
    kb_context["project_defaults"] = project_defaults
    L2 = build_L2_context(boq_item, kb_context, feature_comparison)
    q0_match = boq_item.get("q0_match", {})
    if q0_match:
        trace_text = " -> ".join(
            f"{x.get('try_code')}({x.get('level')}:{x.get('match_count')})"
            for x in q0_match.get("trace", [])
        )
        print(
            f"Q0国标命中: {q0_match.get('match_type', '')} | "
            f"输入{boq_item.get('code', '')} -> 标准{boq_item.get('standard_code', '')} "
            f"{boq_item.get('standard_name', '')} | 轨迹: {trace_text}"
        )
        if feature_comparison.get("standard_feature_comparison"):
            cmp = feature_comparison["standard_feature_comparison"]
            print(f"标准特征对比: 命中{len(cmp.get('matched', []))}项, 缺少/未明示{len(cmp.get('missing_expected', []))}项, 实际补充{len(cmp.get('extra_input', []))}项")
    if kb_context.get("quota_candidates"):
        print(
            f"Q1/Q2定额上下文: 候选定额{len(kb_context.get('quota_candidates', []))}条, "
            f"材料消耗{len(kb_context.get('quota_materials', []))}条"
        )
    print(
        f"知识库命中: 工序规则{len(kb_context['process_rules'])}条, "
        f"材料映射{len(kb_context['material_rules'])}条, "
        f"分类规则{len(kb_context['classification_rules'])}条, "
        f"损耗规则{len(kb_context['loss_rules'])}条, "
        f"人工确认规则{len(kb_context.get('approved_rules', []))}条"
    )
    if project_defaults.get("matched_defaults"):
        rec_count = sum(len(x.get("recommendations", [])) for x in project_defaults.get("matched_defaults", []))
        print(
            f"项目默认推荐库: 命中体系{len(project_defaults.get('matched_defaults', []))}个, "
            f"推荐候选{rec_count}条 | 查询: {project_defaults.get('query', {})}"
        )
    elif project_defaults.get("risk_notes"):
        print("项目默认推荐库: 未注入材料候选 | " + "；".join(project_defaults.get("risk_notes", [])[:2]))

    print("\n=== Stage 0: Meta PromptPlan ===")
    strategy = run_meta_strategy(boq_item, kb_context, feature_comparison, feature_materials, L1, L2)
    t1_section = boq_item.get("t1_section", "") or "未分类清单"
    print(f"清单类型: {t1_section}")
    if strategy.risk_flags:
        print(f"风险标记: {'; '.join(strategy.risk_flags)}")
    if strategy.must_include_materials:
        print("必须纳入材料: " + "、".join(strategy.must_include_materials))
    if strategy.forbidden_materials:
        print(f"禁止材料: {'、'.join(strategy.forbidden_materials)}")
    if strategy.knowledge_conflicts:
        print(f"知识冲突: {len(strategy.knowledge_conflicts)}项")

    # Step 1: 工序还原。每条清单都调用 Kimi 做标准清单/项目特征/知识库对比推演。
    print("\n=== Step 1: 标准清单与工序推演 ===")
    raw1 = ""
    try:
        raw1 = call_ai(
            "Step 1 标准清单与工序推演",
            [
                {
                    "role": "system",
                    "content": stage_system_prompt(
                        strategy,
                        "stage1",
                        "你是建筑工程清单拆解专家。必须基于标准清单、项目特征和知识库上下文做业务推演，并输出可审计的JSON结果。不要输出JSON之外的内容。",
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"L1项目上下文:{json.dumps(L1, ensure_ascii=False, separators=(',', ':'))}\n\n"
                        f"L2清单上下文:{json.dumps(L2, ensure_ascii=False, separators=(',', ':'))}\n\n"
                        f"Meta PromptPlan:{json.dumps({'must_include_materials': strategy.must_include_materials, 'forbidden_materials': strategy.forbidden_materials, 'risk_flags': strategy.risk_flags, 'knowledge_conflicts': strategy.knowledge_conflicts, 'quantity_strategy': strategy.quantity_strategy}, ensure_ascii=False, separators=(',', ':'))}\n\n"
                        f"项目默认推荐提示:{L2.get('project_defaults', {}).get('prompt_injection', '')}\n\n"
                        "请完成以下推演：\n"
                        "1. 先判断输入清单编码是否归属于 standard_code 对应的标准分部分项。\n"
                        "2. 再对比项目特征，判断它具体是在做什么工作。\n"
                        "3. 再结合 process_rules 判断本条清单需要哪些施工工序。\n"
                        "4. 若知识库工序与项目特征冲突，必须以项目特征为准并说明原因。\n"
                        "5. 项目默认推荐库只能帮助识别常规做法边界，不能覆盖项目特征或强行补材料。\n"
                        "6. 输出必须包含 standard_match.comparison_summary 和每个工序的 evidence。\n\n"
                        f"最终返回JSON格式：{PROCESS_OUTPUT_EXAMPLE}"
                    ),
                },
            ],
            max_tokens=2048,
        )
        print("raw1 first 200:", raw1[:200])
        try:
            processes = normalize_process_schema(extract_json(raw1))
            if is_placeholder_payload(processes, "processes"):
                raise ValueError("解析到 prompt 示例工序 JSON")
            if not processes.get("processes"):
                raise ValueError("模型返回工序为空")
        except ValueError as e:
            print(f"Step 1 未返回标准JSON，启用当前清单上下文兜底：{e}")
            processes = build_processes_from_text_or_context(raw1, boq_item, kb_context)
    except RuntimeError as e:
        print(f"Step 1 AI调用失败，启用当前清单上下文兜底：{e}")
        processes = build_processes_from_text_or_context(raw1, boq_item, kb_context)

    # 归一化工序列表：兼容模型返回字符串而非对象的情况
    raw_list = processes.get("processes", [])
    normalized = []
    for i, p in enumerate(raw_list):
        if isinstance(p, str):
            normalized.append({
                "step": i + 1,
                "name": p,
                "description": p,
                "is_main": True,
                "typical_materials": "",
            })
        elif isinstance(p, dict):
            if "step" not in p:
                p["step"] = i + 1
            normalized.append(p)
        else:
            normalized.append({
                "step": i + 1,
                "name": str(p),
                "description": str(p),
                "is_main": True,
                "typical_materials": "",
            })
    processes["processes"] = normalized

    print(f"工序数: {len(processes.get('processes', []))}")
    for p in processes.get("processes", []):
        print(f"  {p.get('step')}. {p.get('name')} [{'主工序' if p.get('is_main') else '辅助'}]: {p.get('description', '')[:60]}")

    # Stage 2: per-process 材料候选推导（asyncio 并发，默认上限2，避免触发 Kimi 组织并发限制）
    print("\n=== Stage 2: 材料推导（per-process 并发） ===")
    if feature_materials.get("materials"):
        print("实际特征明示材料: " + "、".join(m["material_name"] for m in feature_materials["materials"]))
    compact_processes = [
        {
            "step": p.get("step"),
            "name": p.get("name"),
            "is_main": p.get("is_main"),
            "typical_materials": p.get("typical_materials", ""),
        }
        for p in processes.get("processes", [])
    ]

    if RUN_AI_MATERIALS:
        # 只对主工序做并发推导
        main_processes = [p for p in compact_processes if p.get("is_main")]
        if not main_processes:
            main_processes = compact_processes[:3]  # 至少取前3个

        if len(main_processes) > 1:
            print(f"  启动 {len(main_processes)} 个工序推导（并发上限{STAGE2_MAX_CONCURRENCY}，失败自动重试{AI_MAX_RETRIES}次）...")

            async def _run_stage2():
                semaphore = asyncio.Semaphore(STAGE2_MAX_CONCURRENCY)
                tasks = [
                    derive_materials_for_process(
                        p, boq_item, feature_comparison, strategy,
                        L1, L2, semaphore,
                    )
                    for p in main_processes
                ]
                return await asyncio.gather(*tasks)

            process_results = asyncio.run(_run_stage2())
            success = sum(1 for r in process_results if not r.get("error"))
            failed = sum(1 for r in process_results if r.get("error"))
            print(f"  并发完成: {success}成功, {failed}失败")
            materials = merge_per_process_materials(process_results)
        else:
            # 只有一个工序，直接同步调用
            print("  仅1个主工序，同步调用...")
            raw2 = ""
            try:
                raw2 = call_ai(
                    "Stage 2 AI材料候选推理",
                    [
                        {
                            "role": "system",
                            "content": stage_system_prompt(
                                strategy,
                                "stage2",
                                "你是建筑工程材料拆解专家。不要输出分析过程，只输出最终JSON对象。",
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"L1项目上下文:{json.dumps(L1, ensure_ascii=False, separators=(',', ':'))}\n"
                                f"L2清单上下文:{json.dumps(L2, ensure_ascii=False, separators=(',', ':'))}\n"
                                f"施工工序:{json.dumps(compact_processes, ensure_ascii=False, separators=(',', ':'))}\n\n"
                                f"Meta PromptPlan:{json.dumps({'must_include_materials': strategy.must_include_materials, 'forbidden_materials': strategy.forbidden_materials, 'risk_flags': strategy.risk_flags, 'knowledge_conflicts': strategy.knowledge_conflicts, 'quantity_strategy': strategy.quantity_strategy}, ensure_ascii=False, separators=(',', ':'))}\n\n"
                                f"项目默认推荐提示:{L2.get('project_defaults', {}).get('prompt_injection', '')}\n\n"
                                "任务：基于清单信息和工序链做真实材料推演。\n"
                                "若采纳项目默认推荐库的候选材料，必须额外输出 source_type=project_default_kb，且 confidence 不得高于 medium；默认推荐不得覆盖项目特征明示材料。\n"
                                "关键约束：若输入项目特征明示了具体材料材质/类型（如「钢制保温门」），则材料名称必须保留该材质/类型，"
                                "不得替换为 T3 库中或默认推荐库中材质不同的近似材料。"
                                "若 T3 库无对应条目，保留原始名称并标记 confidence=low。\n"
                                "不要输出JSON之外的任何文字。\n"
                                f"最终返回JSON格式：{MATERIAL_OUTPUT_EXAMPLE}"
                            ),
                        },
                    ],
                    max_tokens=2048,
                )
                try:
                    materials = normalize_material_schema(extract_json(raw2))
                    if not materials.get("materials"):
                        materials = build_materials_from_text_or_kb(raw2, kb_context)
                except ValueError:
                    materials = build_materials_from_text_or_kb(raw2, kb_context)
            except RuntimeError as e:
                print(f"Stage 2 AI调用失败: {e}")
                materials = {"materials": []}

        # 合并显性材料 + PromptPlan强制材料
        materials = merge_material_candidates(
            feature_materials,
            materials_from_project_defaults(project_defaults, boq_item),
            materials_from_strategy(strategy),
            materials,
        )
        materials = filter_forbidden_materials(materials, strategy)  # 代码级屏蔽禁止材料
    else:
        default_materials_obj = materials_from_project_defaults(project_defaults, boq_item)
        if kb_context["material_rules"] or feature_materials.get("materials") or default_materials_obj.get("materials"):
            print("RUN_AI_MATERIALS=0，仅使用显性材料/默认推荐库/知识库材料映射规则。")
            materials = merge_material_candidates(
                feature_materials,
                default_materials_obj,
                materials_from_strategy(strategy),
                infer_materials_by_rules(processes, kb_context),
            )
            materials = filter_forbidden_materials(materials, strategy)
        else:
            raise RuntimeError("当前清单未命中显性材料、默认推荐库或材料知识库，不能关闭 AI 材料推导。")

# Stage 4: T3标准化 + N5/N6规范校验（前置到算量之前）
    print("\n=== Stage 4: T3标准化与规范校验 ===")
    materials, validation_issues = validate_and_constrain_materials(materials, kb_context)
    materials = filter_forbidden_materials(materials, strategy)  # Stage4回填后再次剔除禁止材料
    print(f"知识库校验: 问题{len(validation_issues)}条")
    for issue in validation_issues:
        print(f"  - {issue}")

    print(f"材料数: {len(materials.get('materials', []))}")
    for m in materials.get("materials", []):
        standard_text = format_standard_refs(m.get("standard_refs", []), m.get("standard_code", "")) or "未绑定"
        standard_status = m.get("standardization_status", "unmatched")
        display_spec = derive_display_spec(m)
        print(
            f"  {m.get('material_name')} | 规格:{display_spec} | {m.get('unit', '')} | {m.get('role', '')} | "
            f"{m.get('category_l1', '')}/{m.get('category_l2', '')} | "
            f"损耗{m.get('waste_rate_estimate', None)} | 标准:{standard_text} | "
            f"标准化:{standard_status}({m.get('standardization_score', '')}) | "
            f"{m.get('confidence', '')} | 来源:{m.get('source', '')}"
        )

    # 材料命名+规格合规校验（基于技能规则库）
    print("\n=== Stage 4.5: 材料命名与规格校验 ===")
    import importlib.util as _iu
    _spec = _iu.spec_from_file_location(
        "材料命名校验",
        os.path.join(os.path.dirname(__file__), "工具脚本", "材料命名校验.py")
    )
    _mod = _iu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    _mod.validate_materials_batch(materials.get("materials", []))
    _mod.validate_spec_batch(materials.get("materials", []))
    _mod.print_validation_report(materials.get("materials", []))

    # Stage 3: 纯数学用量计算（损耗率优先级：T2规则 > 品类树 > pending）
    print("\n=== Stage 3: 确定性用量计算 ===")

    # 将项目特征中提取的规格参数注入主设备材料，确保特征原文中的规格不丢失
    feature_specs = extract_feature_spec_params(boq_item.get("feature_text", ""))
    boq_clean_name = compact_text(boq_item.get("name", ""))
    if feature_specs and boq_clean_name:
        for m in materials.get("materials", []):
            m_name_compact = compact_text(m.get("material_name", ""))
            # 如果材料名与清单名称核心部分匹配（前6个字），注入特征规格
            if len(boq_clean_name) >= 6 and len(m_name_compact) >= 6:
                if boq_clean_name[:6] in m_name_compact or m_name_compact[:6] in boq_clean_name:
                    if not m.get("spec_hint"):
                        m["spec_hint"] = feature_specs
                    break

    calculations = []
    for m in materials.get("materials", []):
        row = calculate_material_quantity(boq_item, m, kb_context)
        calculations.append(row)
        spec = derive_display_spec(m)
        if row.get("needs_review"):
            qty_text = ""
            if row.get("procurement_qty") is not None:
                qty_text = (
                    f" | 已按规则计算: {row.get('design_qty')} {row.get('unit', '')} "
                    f"+ 损耗{(row.get('loss_rate') or 0)*100:.1f}% = "
                    f"{row.get('procurement_qty')} {row.get('unit', '')}"
                )
            print(f"  {row['material_name']} [{spec}]: 待复核{qty_text} | {row.get('notes', '')}")
        else:
            print(
                f"  {row['material_name']} [{spec}]: {row.get('formula', '')} = "
                f"{row.get('design_qty')} {row.get('unit', '')} | "
                f"损耗{row.get('loss_rate', 0)*100:.1f}% | 采购{row.get('procurement_qty')} {row.get('unit', '')}"
            )

    # ── 最终采购清单汇总 ──
    print("\n" + "=" * 70)
    print("采购清单汇总")
    print("=" * 70)
    print(f"{'材料名称':<16} {'规格型号':<22} {'数量':>10} {'单位':<6} {'备注':<20}")
    print("-" * 70)
    for m, row in zip(materials.get("materials", []), calculations):
        name = m.get("material_name", "?")[:16]
        spec = derive_display_spec(m)[:22]
        qty = row.get("procurement_qty") or row.get("design_qty") or "待定"
        unit = row.get("unit") or m.get("unit", "") or "?"
        qty_str = f"{float(qty):.3f}" if isinstance(qty, (int, float)) else str(qty)
        notes = []
        if row.get("needs_review"):
            notes.append("需复核")
        if m.get("standardization_status") == "unmatched":
            notes.append("非T3标准")
        if m.get("confidence") == "low":
            notes.append("低置信度")
        note_str = ",".join(notes)[:20] if notes else m.get("source", "")[:20]
        print(f"{name:<16} {spec:<22} {qty_str:>10} {unit:<6} {note_str:<20}")
    print("=" * 70)

    review = None
    if RUN_REVIEW:
        print("\n=== Stage 5: AI审核评估（只读 — 不改数据） ===")
        material_view = [
            {
                "material": build_L4_context(m, kb_context),
                "calculation": calculations[idx] if idx < len(calculations) else {},
            }
            for idx, m in enumerate(materials.get("materials", []))
        ]
        review_input = [
            {
                "material_name": m.get("material_name"),
                "role": m.get("role"),
                "unit": m.get("unit"),
            }
            for m in materials.get("materials", [])
        ]
        raw5 = call_ai(
            "Step 5 审核评估",
            [
                {
                    "role": "system",
                    "content": stage_system_prompt(
                        strategy,
                        "stage5",
                        "你是建筑工程质量审核专家。审核采购材料清单是否有明显漏项。只返回合法JSON对象。审核只读，不得改写计算结果。",
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"审核材料清单:{json.dumps(review_input, ensure_ascii=False, separators=(',', ':'))}\n"
                        f"材料标准化与计算视图:{json.dumps(material_view, ensure_ascii=False, separators=(',', ':'))}\n"
                        f"施工工序:{json.dumps([p.get('name') for p in processes.get('processes', [])], ensure_ascii=False, separators=(',', ':'))}\n"
                        "返回格式: {\"has_missing\": true/false, \"missing\": [\"材料名\"], \"risk\": \"低/中/高\", \"comment\": \"不超过40字\", \"suggestions\": []}"
                    ),
                },
            ],
            max_tokens=800,
        )
        review = ensure_object_with_key(extract_json(raw5), "review")
    else:
        print("\n=== Stage 5: AI审核评估（只读 — 不改数据） ===")
        print("已跳过。需要审核时执行：RUN_REVIEW=1 python3 test_ai_pipeline.py")

    accuracy_review = build_accuracy_review(boq_item, feature_materials, materials, calculations, strategy)
    print("\n=== Stage 6: 确定性准确性门禁 ===")
    print(f"标准匹配风险: {accuracy_review['standardMatchRisk']} | 需要人工复核: {accuracy_review['needsHumanReview']}")
    if accuracy_review.get("codeNameConflict"):
        print(f"  编码名称冲突: {accuracy_review['codeNameConflict'].get('message', '')}")
    if accuracy_review["missingExplicitMaterials"]:
        print("  显性材料遗漏: " + "、".join(accuracy_review["missingExplicitMaterials"]))
    for item in accuracy_review["quantityParamsMissing"]:
        print(f"  数量参数缺失: {item['material_name']} -> {';'.join(item.get('missing_params', []))}")
    if accuracy_review.get("defaultParamReviewMaterials"):
        print("  使用默认参数需复核: " + "、".join(accuracy_review["defaultParamReviewMaterials"]))
    if accuracy_review["unsupportedAiOnlyMaterials"]:
        print("  仅AI推理且未命中知识库/显性材料: " + "、".join(accuracy_review["unsupportedAiOnlyMaterials"]))
    if accuracy_review["unstandardizedMaterials"]:
        print("  未通过标准物料校验: " + "、".join(accuracy_review["unstandardizedMaterials"]))
    if accuracy_review["standardsMissing"]:
        print("  已标准化但缺少规范绑定: " + "、".join(accuracy_review["standardsMissing"]))
    if accuracy_review["nameOrStandardReviewMaterials"]:
        print("  名称/适用范围需复核: " + "、".join(accuracy_review["nameOrStandardReviewMaterials"]))
    if accuracy_review.get("projectDefaultReviewMaterials"):
        print("  默认推荐库候选需复核: " + "、".join(accuracy_review["projectDefaultReviewMaterials"]))
    if accuracy_review.get("missingLossRateMaterials"):
        print("  损耗率依据缺失: " + "、".join(accuracy_review["missingLossRateMaterials"]))

    return {
        "boq_item": boq_item,
        "strategy": strategy,
        "processes": processes.get("processes", []),
        "materials": materials.get("materials", []),
        "calculations": calculations,
        "validation_issues": validation_issues,
        "accuracy_review": accuracy_review,
        "review": review,
        "kb_context": kb_context,
    }


# -----------------------------
# 主流程：支持单条或多条清单
# -----------------------------
L1_WITHOUT = build_L1_context(project_config=None, cli_args=None)
# 先用 CLI args 构建 L1；如果命令行没有项目信息，用交互采集的补上
L1_WITH_PROJECT = build_L1_context(project_config=None, cli_args=args)
_CLI_HAS_PROJECT = bool(
    args.project_category or args.structure_type
    or args.height_scope or args.project_region
)
if _INTERACTIVE_PROJECT_INFO and not _CLI_HAS_PROJECT:
    L1_WITH_PROJECT = build_L1_context(project_config=_INTERACTIVE_PROJECT_INFO, cli_args=None)
HAS_PROJECT_INFO = bool(
    L1_WITH_PROJECT.get("project_category")
    or L1_WITH_PROJECT.get("structure_type")
    or L1_WITH_PROJECT.get("height_scope")
    or L1_WITH_PROJECT.get("region")
)

if args.compare and not HAS_PROJECT_INFO:
    print("\n⚠️ --compare 需要项目信息，但未提供 --project-category/--structure-type/--height-scope/--project-region")
    print("  请添加项目参数后重试。对比模式已跳过。")

if args.compare and HAS_PROJECT_INFO:
    # ── 对比模式：分别以有/无项目信息各跑一次 ──────────────────
    print("\n" + "╔" + "═" * 78 + "╗")
    print("║  对比模式：先以【无项目信息】运行，再以【有项目信息】运行       ║")
    print("╚" + "═" * 78 + "╝")

    print(f"\n{'='*60}")
    print("  第一轮：无项目信息（L1 为空）")
    print(f"{'='*60}")
    results_without = []
    for idx, item in enumerate(BOQ_ITEMS, start=1):
        result = process_boq_item(item, idx, L1_override=L1_WITHOUT)
        results_without.append(result)

    print(f"\n{'='*60}")
    print("  第二轮：有项目信息")
    print(f"  project_category={L1_WITH_PROJECT.get('project_category', '(空)')}  "
          f"structure_type={L1_WITH_PROJECT.get('structure_type', '(空)')}  "
          f"height_scope={L1_WITH_PROJECT.get('height_scope', '(空)')}  "
          f"region={L1_WITH_PROJECT.get('region', '(空)')}")
    print(f"{'='*60}")
    results_with = []
    for idx, item in enumerate(BOQ_ITEMS, start=1):
        result = process_boq_item(item, idx, L1_override=L1_WITH_PROJECT)
        results_with.append(result)

    # ── 输出对比摘要 ──────────────────────────────────────────
    print("\n" + "╔" + "═" * 78 + "╗")
    print("║  对比摘要：无项目信息 vs 有项目信息                            ║")
    print("╚" + "═" * 78 + "╝")

    for idx in range(len(BOQ_ITEMS)):
        r_off = results_without[idx]
        r_on = results_with[idx]
        item = BOQ_ITEMS[idx]
        print(f"\n── 清单 {idx+1}: {item.get('code','')} {item.get('name','')} ──")

        # 项目默认推荐库命中对比
        pd_off = (r_off.get("kb_context", {}) or {}).get("project_defaults", {})
        pd_on = (r_on.get("kb_context", {}) or {}).get("project_defaults", {})
        sys_off = len(pd_off.get("matched_defaults", []))
        sys_on = len(pd_on.get("matched_defaults", []))
        rec_off = sum(len(s.get("recommendations", [])) for s in pd_off.get("matched_defaults", []))
        rec_on = sum(len(s.get("recommendations", [])) for s in pd_on.get("matched_defaults", []))
        risk_off = pd_off.get("risk_notes", [])
        risk_on = pd_on.get("risk_notes", [])

        print(f"  【项目默认推荐库命中】")
        print(f"    无项目信息: {sys_off} 体系, {rec_off} 条推荐候选"
              + (f" | 风险: {'; '.join(risk_off[:2])}" if risk_off else ""))
        print(f"    有项目信息: {sys_on} 体系, {rec_on} 条推荐候选"
              + (f" | 风险: {'; '.join(risk_on[:2])}" if risk_on else ""))
        if sys_off != sys_on or rec_off != rec_on:
            delta_sys = sys_on - sys_off
            delta_rec = rec_on - rec_off
            direction = "增加" if delta_sys >= 0 else "减少"
            print(f"    → 差异: 体系 {direction} {abs(delta_sys)} 个, 推荐 {direction} {abs(delta_rec)} 条")

        # 最终材料对比
        mats_off = {m.get("material_name", ""): m for m in r_off.get("materials", [])}
        mats_on = {m.get("material_name", ""): m for m in r_on.get("materials", [])}
        only_off = set(mats_off) - set(mats_on)
        only_on = set(mats_on) - set(mats_off)
        common = set(mats_off) & set(mats_on)

        print(f"  【最终材料】")
        print(f"    无项目信息: {len(mats_off)} 条 | 有项目信息: {len(mats_on)} 条")
        if only_off:
            print(f"    仅无项目信息出现: {', '.join(sorted(only_off)[:8])}")
        if only_on:
            print(f"    仅有项目信息出现: {', '.join(sorted(only_on)[:8])}")

        # 默认推荐库来源的材料差异
        pd_src_off = {n for n, m in mats_off.items() if m.get("source_type") == "project_default_kb"}
        pd_src_on = {n for n, m in mats_on.items() if m.get("source_type") == "project_default_kb"}
        if pd_src_off or pd_src_on:
            print(f"  【来自默认推荐库的材料】")
            print(f"    无项目信息: {len(pd_src_off)} 条" + (f" ({', '.join(sorted(pd_src_off)[:8])})" if pd_src_off else ""))
            print(f"    有项目信息: {len(pd_src_on)} 条" + (f" ({', '.join(sorted(pd_src_on)[:8])})" if pd_src_on else ""))

        # 准确度门禁对比
        ar_off = r_off.get("accuracy_review", {})
        ar_on = r_on.get("accuracy_review", {})
        sev_off = len(ar_off.get("severeIssues", []))
        sev_on = len(ar_on.get("severeIssues", []))
        rev_off = ar_off.get("needsHumanReview", False)
        rev_on = ar_on.get("needsHumanReview", False)
        if sev_off != sev_on or rev_off != rev_on:
            print(f"  【准确度门禁变化】")
            print(f"    严重问题: {sev_off} → {sev_on} | 需人工复核: {rev_off} → {rev_on}")

    # 写入对比结果
    if args.output:
        out_path = args.output
        base, ext = os.path.splitext(out_path)
        path_without = f"{base}_无项目信息{ext}"
        path_with = f"{base}_有项目信息{ext}"
        with open(path_without, "w", encoding="utf-8") as f:
            json.dump({"results": results_without, "L1": L1_WITHOUT}, f, ensure_ascii=False, indent=2)
        with open(path_with, "w", encoding="utf-8") as f:
            json.dump({"results": results_with, "L1": L1_WITH_PROJECT}, f, ensure_ascii=False, indent=2)
        print(f"\n对比结果已分别写入:\n  {path_without}\n  {path_with}")

else:
    # ── 普通模式：单次运行 ─────────────────────────────────────
    print(f"\n待处理清单数: {len(BOQ_ITEMS)}")
    if HAS_PROJECT_INFO:
        print(f"项目信息: category={L1_WITH_PROJECT.get('project_category', '(空)')}  "
              f"structure={L1_WITH_PROJECT.get('structure_type', '(空)')}  "
              f"height={L1_WITH_PROJECT.get('height_scope', '(空)')}  "
              f"region={L1_WITH_PROJECT.get('region', '(空)')}")
    results = []
    for idx, item in enumerate(BOQ_ITEMS, start=1):
        result = process_boq_item(item, idx, L1_override=L1_WITH_PROJECT if HAS_PROJECT_INFO else L1_WITHOUT)
        results.append(result)
        if args.learn:
            learned_rule = collect_learning_feedback(item, result, APPROVED_KB)
            if learned_rule:
                save_approved_kb(APPROVED_KB_PATH, APPROVED_KB)
                print(f"人工确认知识已写回: {APPROVED_KB_PATH}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({"results": results}, f, ensure_ascii=False, indent=2)
        print(f"\n结果已写入: {args.output}")

    has_severe = any(r.get("accuracy_review", {}).get("severeIssues") for r in results)
    needs_review = any(r.get("accuracy_review", {}).get("needsHumanReview") for r in results)
    if has_severe:
        print("\n===== 流程完成，但存在严重准确性问题，禁止自动通过 =====")
    elif needs_review:
        print("\n===== 流程完成，存在待复核项，不能视为完全通过 =====")
    else:
        print("\n===== 全流程准确性门禁通过 =====")
