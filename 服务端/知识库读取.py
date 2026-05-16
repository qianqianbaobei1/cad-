"""知识库数据读取层 — 本地索引优先，O(1)查找，CSV兜底"""
import csv
import json
import re
import threading
from pathlib import Path
from collections import defaultdict

from 配置 import KB_01, KB_FJ, KB_AZ, KB_NORM, KB_CAT, KB_PROV, 内部知识库_DIR, DATA_DIR

# ══════════════════════════════════════════════
# 本地索引目录
# ══════════════════════════════════════════════

索引目录 = DATA_DIR / "本地知识库包" / "索引"

# ══════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════

def read_csv(path: Path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def read_json(path: Path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)

# ══════════════════════════════════════════════
# 索引加载（持久缓存）
# ══════════════════════════════════════════════

_索引缓存 = {}
_索引锁 = threading.Lock()

def _读索引(name: str) -> dict:
    if name in _索引缓存:
        return _索引缓存[name]
    with _索引锁:
        if name in _索引缓存:
            return _索引缓存[name]
        data = read_json(索引目录 / f"{name}.json")
        _索引缓存[name] = data if data is not None else {}
        return _索引缓存[name]

# ══════════════════════════════════════════════
# CSV 内存缓存（启动时加载一次，用于列表/搜索类查询）
# ══════════════════════════════════════════════

_csv_缓存 = {}
_csv_锁 = threading.Lock()

def _读CSV缓存(key: str, path: Path):
    if key in _csv_缓存:
        return _csv_缓存[key]
    with _csv_锁:
        if key in _csv_缓存:
            return _csv_缓存[key]
        _csv_缓存[key] = read_csv(path)
        return _csv_缓存[key]

# ══════════════════════════════════════════════
# T3 物料库
# ══════════════════════════════════════════════

def get_t3_materials(search="", page=1, per_page=50):
    """获取T3标准物料列表，支持搜索和分页"""
    rows = (_读CSV缓存("t3_fj", KB_FJ / "CSV导出/03_t3_标准物料库.csv") +
            _读CSV缓存("t3_az", KB_AZ / "CSV导出/02_t3_标准物料库.csv"))
    if search:
        s = search.lower()
        rows = [r for r in rows if
                s in (r.get("物料ID","")+r.get("标准名称","")+r.get("分类(一级)","")+r.get("分类(二级)","")+r.get("分类(三级)","")).lower()]
    total = len(rows)
    start = (page - 1) * per_page
    return {"total": total, "page": page, "per_page": per_page, "items": rows[start:start+per_page]}

def get_t3_detail(material_id: str):
    """获取单个T3物料详情 — O(1) 索引查找"""
    for idx_name in ("t3_fj_按物料ID", "t3_az_按物料ID"):
        idx = _读索引(idx_name)
        if material_id in idx:
            return idx[material_id]
    # 索引未命中时回退到 CSV 遍历
    rows = _读CSV缓存("t3_fj", KB_FJ / "CSV导出/03_t3_标准物料库.csv") + \
           _读CSV缓存("t3_az", KB_AZ / "CSV导出/02_t3_标准物料库.csv")
    for r in rows:
        if r.get("物料ID", "").strip() == material_id:
            return r
    return None

# ══════════════════════════════════════════════
# 品类树
# ══════════════════════════════════════════════

def get_categories(search=""):
    """获取品类节点列表"""
    rows = _读CSV缓存("cat_idx", KB_CAT / "品类节点索引.csv")
    if search:
        s = search.lower()
        rows = [r for r in rows if s in (r.get("display_path","")+r.get("search_keywords","")).lower()]
    return rows[:200]

def get_category_detail(leaf_id: str):
    """获取品类节点详情 — O(1) 索引查找"""
    idx = _读索引("品类_按ID")
    if leaf_id in idx:
        return idx[leaf_id]
    # 回退 CSV
    rows = _读CSV缓存("cat_idx", KB_CAT / "品类节点索引.csv")
    for r in rows:
        if r.get("leaf_id", "").strip() == leaf_id:
            return r
    return None

def get_t3_category_mapping():
    return _读CSV缓存("cat_map", KB_CAT / "T3_品类映射.csv")

# ══════════════════════════════════════════════
# 国家规范库
# ══════════════════════════════════════════════

def get_standards(search="", page=1, per_page=50):
    rows = _读CSV缓存("n1", KB_NORM / "N1_国家规范索引.csv")
    if search:
        s = search.lower()
        rows = [r for r in rows if s in (r.get("standard_id","")+r.get("standard_name","")).lower()]
    total = len(rows)
    start = (page - 1) * per_page
    return {"total": total, "page": page, "per_page": per_page, "items": rows[start:start+per_page]}

def get_standard_detail(standard_id: str):
    rows = _读CSV缓存("n1", KB_NORM / "N1_国家规范索引.csv")
    for r in rows:
        if r.get("standard_id", "").strip() == standard_id:
            n5_all = _读CSV缓存("n5", KB_NORM / "N5_材料规范映射.csv")
            r["linked_materials"] = [n for n in n5_all if n.get("standard_id","") == standard_id]
            return r
    return None

# ══════════════════════════════════════════════
# 定额数据查询 — O(1) 索引优化
# ══════════════════════════════════════════════

def get_quota_list(search="", province="", page=1, per_page=50):
    rows = _读CSV缓存("q1", KB_01 / "Q1_定额索引.csv")
    if province:
        rows = [r for r in rows if r.get("province","") == province]
    if search:
        s = search.lower()
        rows = [r for r in rows if s in (r.get("project_name","")+r.get("quota_code","")+r.get("boq_code","")).lower()]
    total = len(rows)
    start = (page - 1) * per_page
    return {"total": total, "page": page, "per_page": per_page, "items": rows[start:start+per_page]}

def get_quota_detail(quota_id: str):
    """获取定额详情 — O(1) 索引查找"""
    # Q1 索引查找
    q1_idx = _读索引("q1_按ID")
    r = q1_idx.get(quota_id)
    if r is None:
        # 回退 CSV
        rows = _读CSV缓存("q1", KB_01 / "Q1_定额索引.csv")
        for row in rows:
            if row.get("quota_id", "").strip() == quota_id:
                r = row
                break
    if r is None:
        return None
    r = dict(r)  # 不修改索引中的原始数据

    # Q2 索引查找
    q2_idx = _读索引("q2_按定额ID")
    materials = q2_idx.get(quota_id)
    if materials is None:
        q2_all = _读CSV缓存("q2", KB_01 / "Q2_定额材料消耗.csv")
        materials = [q for q in q2_all if q.get("quota_id","") == quota_id]
    r["materials"] = materials or []
    return r

def get_provinces():
    """获取省份列表 — 从 Q1 索引去重"""
    q1_idx = _读索引("q1_按ID")
    provinces = set()
    for row in q1_idx.values():
        p = row.get("province", "")
        if p:
            provinces.add(p)
    if provinces:
        return sorted(provinces)
    # 回退 CSV
    rows = _读CSV缓存("q1", KB_01 / "Q1_定额索引.csv")
    return sorted(set(r.get("province","") for r in rows if r.get("province")))

# ══════════════════════════════════════════════
# 全局搜索
# ══════════════════════════════════════════════

def global_search(query: str):
    s = query.lower()
    results = {"t3": [], "standards": [], "categories": [], "quota": []}

    t3 = get_t3_materials(search=query, per_page=10)
    results["t3"] = [{"id": r.get("物料ID"), "name": r.get("标准名称"),
                       "category": r.get("分类(一级)","")} for r in t3["items"]]

    std = get_standards(search=query, per_page=10)
    results["standards"] = [{"id": r.get("standard_id"), "name": r.get("standard_name")} for r in std["items"]]

    cat = get_categories(search=query)[:10]
    results["categories"] = [{"id": r.get("leaf_id"), "path": r.get("display_path")} for r in cat]

    qt = get_quota_list(search=query, per_page=10)
    results["quota"] = [{"id": r.get("quota_id"), "name": r.get("project_name"),
                          "province": r.get("province")} for r in qt["items"]]

    return results

# ══════════════════════════════════════════════
# 统计信息 — 优先使用索引大小
# ══════════════════════════════════════════════

def get_stats():
    q1_idx = _读索引("q1_按ID")
    q2_idx = _读索引("q2_按定额ID")
    t3_fj_idx = _读索引("t3_fj_按物料ID")
    t3_az_idx = _读索引("t3_az_按物料ID")
    cat_idx = _读索引("品类_按ID")

    q1_count = len(q1_idx) if q1_idx else len(_读CSV缓存("q1", KB_01 / "Q1_定额索引.csv"))
    q2_count = sum(len(v) for v in q2_idx.values()) if q2_idx else len(_读CSV缓存("q2", KB_01 / "Q2_定额材料消耗.csv"))
    t3_fj = len(t3_fj_idx) if t3_fj_idx else len(_读CSV缓存("t3_fj", KB_FJ / "CSV导出/03_t3_标准物料库.csv"))
    t3_az = len(t3_az_idx) if t3_az_idx else len(_读CSV缓存("t3_az", KB_AZ / "CSV导出/02_t3_标准物料库.csv"))
    cat_leaves = len(cat_idx) if cat_idx else len(_读CSV缓存("cat_idx", KB_CAT / "品类节点索引.csv"))

    return {
        "q0_codes": len(_读CSV缓存("q0_fj", KB_01 / "Q0_清单项目编码_房建工程.csv")) +
                    len(_读CSV缓存("q0_az", KB_01 / "Q0_清单项目编码_安装工程.csv")),
        "q1_quota_items": q1_count,
        "q2_consumption_rows": q2_count,
        "q3_mappings": len(_读CSV缓存("q3", KB_01 / "Q3_定额材料映射.csv")),
        "n1_standards": len(_读CSV缓存("n1", KB_NORM / "N1_国家规范索引.csv")),
        "n3_params": len(_读CSV缓存("n3", KB_NORM / "N3_材料技术参数定义.csv")),
        "n6_rules": len(_读CSV缓存("n6", KB_NORM / "N6_规范校验规则.csv")),
        "t3_materials": t3_fj + t3_az,
        "category_leaves": cat_leaves,
        "category_t3_mapped": len(_读CSV缓存("cat_map", KB_CAT / "T3_品类映射.csv")),
        "provinces": len(get_provinces()),
    }

# ══════════════════════════════════════════════
# CCE 三级分类材料库 — 材料名→标准分类匹配
# ══════════════════════════════════════════════

_分类库 = None

def _加载分类库():
    global _分类库
    if _分类库 is None:
        path = 内部知识库_DIR / "三级分类材料库.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                _分类库 = json.load(f)
        else:
            _分类库 = {"categories": [], "name_index": {}}
    return _分类库

def _归一(text: str) -> str:
    text = str(text or "").strip().lower()
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"[\s　,，;；、。/\\]+", "", text)

def 匹配材料到分类(材料名: str) -> dict:
    """将输入的材料名匹配到 CCE 三级分类。返回匹配结果或 None。"""
    kb = _加载分类库()
    name_index = kb.get("name_index", {})
    key = _归一(材料名)
    exact = name_index.get(key, [])
    if exact:
        row = exact[0]
        return {
            "匹配状态": "精确匹配",
            "标准材料名": row.get("material_name", ""),
            "末级分类ID": row.get("category_id", ""),
            "末级分类名称": row.get("category_l3", ""),
            "分类路径": row.get("category_path", ""),
            "匹配依据": row.get("source", ""),
            "候选数量": len(exact),
        }
    candidates = []
    for idx_key, rows in name_index.items():
        if not idx_key:
            continue
        if idx_key in key or key in idx_key:
            candidates.extend(rows[:3])
    if candidates:
        candidates.sort(key=lambda r: len(r.get("material_name", "")), reverse=True)
        row = candidates[0]
        return {
            "匹配状态": "包含匹配",
            "标准材料名": row.get("material_name", ""),
            "末级分类ID": row.get("category_id", ""),
            "末级分类名称": row.get("category_l3", ""),
            "分类路径": row.get("category_path", ""),
            "匹配依据": row.get("source", ""),
            "候选数量": len(candidates),
        }
    return {
        "匹配状态": "未命中",
        "标准材料名": "",
        "末级分类ID": "",
        "末级分类名称": "",
        "分类路径": "",
        "匹配依据": "",
        "候选数量": 0,
    }

def 获取分类库统计() -> dict:
    kb = _加载分类库()
    summary = kb.get("summary", {})
    return {
        "分类总数": summary.get("category_count", 0),
        "有材料分类数": summary.get("category_with_material_count", 0),
        "材料总数": summary.get("material_count", 0),
        "别名总数": summary.get("alias_count", 0),
        "索引条目数": summary.get("name_index_count", 0),
    }
