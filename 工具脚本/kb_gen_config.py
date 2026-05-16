"""知识库生成配置 — 定义知识库构建流程中的路径映射、数据源、处理规则"""
import json
from pathlib import Path

# ── 路径配置 ──
BASE_DIR = Path(__file__).resolve().parent.parent
KB_SOURCE_DIR = Path("/Users/qianqianawodebaobei/Desktop/智能清单/知识库")
STANDARD_KB_DIR = BASE_DIR / "标准知识库" / "源数据"
DATA_DIR = BASE_DIR / "项目数据"
INDEX_DIR = DATA_DIR / "本地知识库包" / "索引"

# ── CSV源路径映射：{源表名: (源知识库子目录, 文件名)} ──
# 优先级: 标准知识库(源数据/) > 主知识库(知识库/)
CSV_SOURCES: dict[str, list[tuple[str, str]]] = {
    "q0_fj": [
        ("01_定额库", "Q0_清单项目编码_房建工程.csv"),
        ("01_定额库", "Q0_清单项目编码_房建工程_v2.csv"),
    ],
    "q0_az": [
        ("01_定额库", "Q0_清单项目编码_安装工程.csv"),
    ],
    "q1": [
        ("01_定额库", "Q1_定额索引.csv"),
    ],
    "q2": [
        ("01_定额库", "Q2_定额材料消耗.csv"),
    ],
    "q3": [
        ("01_定额库", "Q3_定额材料映射.csv"),
    ],
    "t3_fj": [
        ("01_房屋建筑与装饰工程", "CSV导出/03_t3_标准物料库.csv"),
    ],
    "t3_az": [
        ("02_通用安装工程", "CSV导出/02_t3_标准物料库.csv"),
    ],
    "t1_fj": [
        ("01_房屋建筑与装饰工程", "CSV导出/01_t1_分部代码路由.csv"),
    ],
    "t1_az": [
        ("02_通用安装工程", "CSV导出/01_t1_分部代码路由.csv"),
    ],
    "t2_fj": [
        ("01_房屋建筑与装饰工程", "CSV导出/02_t2_清单材料映射.csv"),
    ],
    "t2_az": [
        ("02_通用安装工程", "CSV导出/03_t2_清单材料映射.csv"),
    ],
    "n1": [
        ("03_国家规范库", "N1_国家规范索引.csv"),
    ],
    "n3": [
        ("03_国家规范库", "N3_材料技术参数定义.csv"),
    ],
    "n5": [
        ("03_国家规范库", "N5_材料规范映射.csv"),
    ],
    "n6": [
        ("03_国家规范库", "N6_规范校验规则.csv"),
    ],
    "cat_idx": [
        ("06_品类树", "品类节点索引.csv"),
    ],
    "cat_map": [
        ("06_品类树", "T3_品类映射.csv"),
    ],
    "province_config": [
        ("省份适配", "CSV导出/06_province_config.csv"),
    ],
    "province_rules": [
        ("省份适配", "CSV导出/07_province_material_rules.csv"),
    ],
    "province_params": [
        ("省份适配", "CSV导出/08_province_param_overrides.csv"),
    ],
    "province_t5": [
        ("省份适配", "CSV导出/09_bj_t5_dimensions.csv"),
    ],
}

# ── 索引构建定义：{索引名: {源表, 键字段, 值字段}} ──
INDEX_DEFINITIONS = {
    "q1_按ID": {
        "source": "q1",
        "key_field": "quota_id",
        "description": "Q1定额索引按quota_id查找",
    },
    "q2_按定额ID": {
        "source": "q2",
        "key_field": "quota_id",
        "value_mode": "list",
        "description": "Q2材料消耗按quota_id分组",
    },
    "t3_fj_按物料ID": {
        "source": "t3_fj",
        "key_field": "物料ID",
        "description": "房建T3标准物料按物料ID查找",
    },
    "t3_az_按物料ID": {
        "source": "t3_az",
        "key_field": "物料ID",
        "description": "安装T3标准物料按物料ID查找",
    },
    "品类_按ID": {
        "source": "cat_idx",
        "key_field": "leaf_id",
        "description": "品类树节点按leaf_id查找",
    },
}

# ── 表头字段定义（用于在校验时检查CSV完整性）──
REQUIRED_COLUMNS: dict[str, list[str]] = {
    "q1": ["quota_id", "province", "project_name", "boq_code"],
    "q2": ["quota_id", "material_name_raw", "quantity", "unit", "map_status"],
    "q3": ["quota_id", "material_name_raw", "material_id"],
    "t3_fj": ["物料ID", "标准名称", "分类(一级)", "分类(二级)", "分类(三级)"],
    "t3_az": ["物料ID", "标准名称", "分类(一级)", "分类(二级)", "分类(三级)"],
    "n1": ["standard_id", "standard_name"],
    "n5": ["material_id", "standard_id"],
    "cat_idx": ["leaf_id", "display_path", "loss_rate"],
    "cat_map": ["material_id", "leaf_id"],
}

# ── 合并配置 ──
MERGE_CONFIG = {
    "t3": {
        "sources": ["t3_fj", "t3_az"],
        "dedup_key": "物料ID",
        "output": "合并_T3_标准物料库.csv",
    },
}

# ── 省份映射 ──
PROVINCE_MAP = {
    "SN": "陕西",
    "SX": "陕西",
    "HB": "湖北",
    "BJ": "北京",
    "GD": "广东",
    "HN": "湖南",
}


def get_source_path(table_name: str) -> Path | None:
    """获取数据表的源文件路径，先查标准知识库，回退主知识库"""
    if table_name not in CSV_SOURCES:
        return None
    for subdir, filename in CSV_SOURCES[table_name]:
        path = STANDARD_KB_DIR / subdir / filename
        if path.exists():
            return path
    # 回退到主知识库
    for subdir, filename in CSV_SOURCES[table_name]:
        path = KB_SOURCE_DIR / subdir / filename
        if path.exists():
            return path
    return None


def list_source_status() -> dict:
    """返回所有数据源的存在状态"""
    status = {}
    for name, paths in CSV_SOURCES.items():
        found = None
        for subdir, filename in paths:
            p = STANDARD_KB_DIR / subdir / filename
            if p.exists():
                found = str(p)
                break
            p = KB_SOURCE_DIR / subdir / filename
            if p.exists():
                found = str(p)
                break
        status[name] = {"found": found is not None, "path": found or ""}
    return status


if __name__ == "__main__":
    print("=== 知识库数据源状态 ===")
    for name, info in list_source_status().items():
        icon = "✅" if info["found"] else "❌"
        print(f"  {icon} {name}: {info['path']}")
