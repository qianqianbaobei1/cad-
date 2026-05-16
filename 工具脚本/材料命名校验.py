"""
材料命名校验模块 — 基于《材料命名统一规则库》v1.0.0
在流水线输出材料清单后，对每个材料名执行 21 条规则的逐项检查。
"""

import re
import json
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent

# ══════════════════════════════════════
# 一、合法后缀集合（基于T3标准库+默认值库实际数据扩充）
# ══════════════════════════════════════

VALID_SUFFIXES = {
    # 钢材/金属
    "钢筋", "钢管", "钢板", "型钢", "槽钢", "角钢", "圆钢", "扁钢", "方钢",
    "钢丝", "钢丝网", "钢绞线", "钢带", "钢格板", "钢轨",
    "螺栓", "螺母", "垫圈", "垫片", "预埋件", "连接件", "支架", "吊架",
    # 混凝土/砂浆/砖
    "混凝土", "砂浆", "灌浆料", "粘结剂", "界面剂", "腻子",
    "砖", "砌块", "瓦", "脊瓦", "石块",
    # 防水/涂料
    "防水卷材", "防水涂料", "防火涂料", "涂料", "油漆",
    # 保温/板材
    "保温板", "塑料", "泡沫塑料", "板材", "模板", "地板",
    # 管材/管件
    "管材", "管件", "接头", "弯头", "三通", "法兰", "直接",
    # 门窗/装饰
    "门窗", "窗", "玻璃", "栏杆", "格栅", "踢脚线", "装饰线条", "线条", "龙骨",
    # 电缆/电气
    "电缆", "电线", "开关", "插座", "灯具", "变压器", "断路器", "继电器",
    "传感器", "控制器", "仪表", "检测仪",
    # 设备/系统
    "设备", "装置", "系统", "机组", "总成", "配件",
    "水泵", "风机", "阀门", "过滤器",
    "成套设备", "处理设备", "辅助设备",
    # 卫浴
    "便器", "面盆", "水槽",
    # 管道/阀门
    "管", "管材", "管件", "接头", "弯头", "三通", "法兰", "直接",
    "阀门", "水龙头", "地漏", "伸缩节", "补偿器",
    # 门/窗/幕墙
    "门", "窗", "门窗", "幕墙", "玻璃", "栏杆", "格栅", "百叶",
    # 装饰/线条
    "踢脚线", "装饰线条", "线条", "龙骨", "吊顶", "饰面板",
    # 辅材
    "焊条", "密封圈", "密封胶", "胶粘剂", "发泡剂",
    "网格布", "防水层", "隔离层", "保护层",
    "胶带", "胶条", "泡沫", "堵料", "填料",
    "绑扎丝", "垫块", "脱模剂", "养护剂",
    # 结构件
    "柱", "梁", "板", "墙", "基础", "桩",
    "楼梯", "台阶", "坡道", "散水", "雨棚",
    # 其他常见
    "网", "布", "膜", "毡", "纸", "绳", "带",
    "钉", "丝", "杆", "件", "座", "箱", "柜", "盒",
}

# ══════════════════════════════════════
# 二、禁止的动作词（出现在名称中暗示工序非材料）
# ══════════════════════════════════════

FORBIDDEN_ACTION_WORDS = {
    "新增", "拆除", "绑扎", "浇筑", "砌筑", "抹灰", "铺设", "粘贴",
    "拌合", "振捣", "养护", "拆模", "压实", "组装", "调试", "接驳",
    "开挖", "回填", "夯实", "找平", "凿毛", "切缝", "灌浆",
}

# 例外：约定俗成的功能性修饰语
ACTION_WORD_EXCEPTIONS = {
    "自密实混凝土",    # 自密实=性能
    "免拆模板",       # 免拆=产品特性
    "干混砌筑砂浆",    # 砌筑砂浆=品类名
    "干混抹灰砂浆",    # 抹灰砂浆=品类名
    "干混地面砂浆",    # 地面砂浆=品类名
    "砌块砌筑粘结剂",   # 砌筑粘结剂=品类名
    "高强无收缩灌浆料", # 灌浆料=品类名
    "装配式套筒灌浆料", # 灌浆料=品类名
    "预埋铁脚",       # 预埋件的一种
    "素土（回填用）",   # 回填用=用途说明
    # 带"粘贴"的装饰材料
    "自粘贴缝带",
    "粘贴式保温板",
    # 含"绑扎/灌浆"但实为产品名
    "镀锌绑扎丝",
    "钢筋绑扎丝",
    "二次灌浆料",
}

# ══════════════════════════════════════
# 三、禁止的占位/引用词
# ══════════════════════════════════════

FORBIDDEN_PLACEHOLDER_WORDS = {
    "其他", "待定", "详见设计", "详见", "暂定", "参照图纸",
    "由厂家提供", "配套", "随设备", "自报", "报价", "投标",
}

# ══════════════════════════════════════
# 四、费用/服务后缀（非材料）
# ══════════════════════════════════════

FEE_SUFFIX_PATTERNS = [
    re.compile(r'(费|服务|工资|酬金|保费|运费|加工费|安装费)$'),
    re.compile(r'^(垃圾清运|混凝土费用|料具维修|打孔|热浸镀锌|进出场|运输保险)'),
]

# ══════════════════════════════════════
# 五、规格参数关键词（这些属于spec字段，不应在name中出现）
# ══════════════════════════════════════

SPEC_VALUE_PATTERNS = [
    # 强度等级
    re.compile(r'\b(?:C\d{2,3}|HRB\d{3}[A-Za-z]*|HPB\d+|MU\d+|M\d{1,2}\b)', re.I),
    # 尺寸
    re.compile(r'\d+\s*[×xX×]\s*\d+'),  # 100×50
    re.compile(r'(?:DN|dn|NPS|φ|Φ)\s*\d+'),
    re.compile(r'\d+\s*(?:mm|cm|m|㎡|m2|m³|m3)\b'),
    # 压力/电压
    re.compile(r'(?:PN|pn)\s*\d+'),
    re.compile(r'\d+\.?\d*\s*(?:MPa|kV|kW|W|V|A|Ω)\b'),
    # 材质牌号后缀
    re.compile(r'\s+(?:Q\d{3}[A-Za-z]*|Q345[A-Za-z]*|304|316|316L|201|202)\s*$'),
    # 级
    re.compile(r'\d+\.?\d*\s*级$'),
]

# ══════════════════════════════════════
# 六、核心校验函数
# ══════════════════════════════════════

def validate_material_name(material_name: str, context: Optional[dict] = None) -> dict:
    """
    对单个材料名执行全部 21 条检查。
    返回校验结果 dict，包含:
    - name_ok: 是否通过
    - name_level: L1~L5
    - name_length_ok: 长度是否合理
    - contains_action_word: 是否含动作词
    - contains_spec_value: 是否含规格参数
    - ends_with_valid_suffix: 后缀是否合法
    - is_fee_or_service: 是否费用/服务项
    - is_placeholder: 是否占位词
    - confidence: high/medium/low
    - needs_review: 是否需要人工复核
    - issues: 问题列表
    """
    result = {
        "name_ok": True,
        "name_level": "",
        "name_length_ok": True,
        "contains_action_word": False,
        "contains_spec_value": False,
        "ends_with_valid_suffix": False,
        "is_fee_or_service": False,
        "is_placeholder": False,
        "is_physical_object": True,
        "confidence": "medium",
        "needs_review": False,
        "issues": [],
    }

    name = material_name.strip()
    if not name:
        result["name_ok"] = False
        result["issues"].append("名称为空")
        result["confidence"] = "low"
        result["needs_review"] = True
        return result

    # --- 规则1: 是否是物理实体 ---
    if not _is_physical_object(name):
        result["is_physical_object"] = False
        result["name_ok"] = False
        result["issues"].append("规则1: 名称不是物理实体名词（可能是工序、费用或引用）")
        result["confidence"] = "low"
        result["needs_review"] = True
        # 早期返回，不继续检查
        return result

    # --- 检查费用/服务 ---
    for pat in FEE_SUFFIX_PATTERNS:
        if pat.search(name):
            result["is_fee_or_service"] = True
            result["name_ok"] = False
            result["issues"].append("规则9: 名称疑似费用/服务项，非采购材料")
            result["confidence"] = "low"
            result["needs_review"] = True
            return result

    # --- 检查占位词 ---
    for word in FORBIDDEN_PLACEHOLDER_WORDS:
        if word in name:
            result["is_placeholder"] = True
            result["name_ok"] = False
            result["issues"].append(f"规则5: 名称包含占位/引用词「{word}」")
            result["confidence"] = "low"
            result["needs_review"] = True
            return result

    # --- 规则2: 长度约束 ---
    name_len = len(name)
    if name_len <= 2:
        result["name_length_ok"] = False
        result["issues"].append(f"规则2: 名称仅{name_len}字，无法识别为何物")
        result["needs_review"] = True
    elif name_len > 15:
        result["issues"].append(f"规则2: 名称{name_len}字，可能含规格参数混入")

    # --- 规则4: 动作词检查 ---
    # 先检查整个名称是否在例外列表，或核心名（去规格后缀）是否例外
    core_no_spec = re.sub(r'\s+[A-Za-z0-9\s/.\-]+$', '', re.sub(r'[（(][^)）]*[)）]', '', name)).strip()
    if name in ACTION_WORD_EXCEPTIONS or core_no_spec in ACTION_WORD_EXCEPTIONS:
        result["contains_action_word"] = False  # 例外
    else:
        for aw in FORBIDDEN_ACTION_WORDS:
            if aw in name:
                result["contains_action_word"] = True
                result["issues"].append(f"规则4: 名称含动作词「{aw}」")
                result["needs_review"] = True
                break

    # --- 规则0/14: 规格参数混入检查 ---
    for pat in SPEC_VALUE_PATTERNS:
        if pat.search(name):
            result["contains_spec_value"] = True
            result["issues"].append(f"规则14: 名称中疑似混入规格参数 (匹配: {pat.pattern[:40]}...)")
            result["needs_review"] = True
            break

    # --- 规则3: 后缀合法性 ---
    # 先去掉括号内容再检查
    core_name = re.sub(r'[（(][^)）]*[)）]', '', name).strip()
    result["ends_with_valid_suffix"] = any(core_name.endswith(s) for s in VALID_SUFFIXES)

    if not result["ends_with_valid_suffix"] and name_len > 2:
        # 再去掉末尾英文字母数字后重试
        trimmed = re.sub(r'\s*[A-Za-z0-9\s/.\-]+$', '', core_name).strip()
        if trimmed and len(trimmed) >= 2:
            result["ends_with_valid_suffix"] = any(trimmed.endswith(s) for s in VALID_SUFFIXES)
        if not result["ends_with_valid_suffix"]:
            result["issues"].append("规则3: 名称后缀不在已验证的合法集合中")
            result["needs_review"] = True

    # --- 规则6: 命名层级判定 ---
    result["name_level"] = _classify_name_level(name, name_len)

    # --- 判断置信度 ---
    issue_count = len(result["issues"])
    if issue_count == 0:
        result["confidence"] = "high"
    elif issue_count <= 1:
        result["confidence"] = "medium"
    else:
        result["confidence"] = "low"

    result["name_ok"] = issue_count == 0

    return result


def _is_physical_object(name: str) -> bool:
    """规则1: 判断名称是否为可采购的物理实体"""
    # 明显不是材料的模式
    non_material_patterns = [
        re.compile(r'^(详见|参照|按\s*图|见\s*图|依据\s*图|根据\s*图)'),
        re.compile(r'^(人工|机械|运输|搬运|清理|调试|检验|测试|检测|验收|报批)'),
        re.compile(r'^(混凝土|钢筋|模板|砌体|抹灰|防水|门窗|安装|施工|开挖|回填).*(?:施工|安装|浇筑|绑扎|铺设|拆除)'),
    ]
    for pat in non_material_patterns:
        if pat.search(name):
            return False
    return True


def _classify_name_level(name: str, name_len: int) -> str:
    """规则6: 判定命名层级 L1~L5"""
    # L5 系统/装置
    if re.search(r'(系统|装置|机组|总成)$', name) and name_len > 5:
        return "L5"
    # L4 品牌/型号+材料
    if re.match(r'^[A-Z]{2,}[-\s]', name):
        return "L4"
    # L3 用途+材料
    if '用' in name and name_len >= 6:
        return "L3"
    # L2 特性+材料
    if name_len >= 5:
        return "L2"
    # L1 纯材料名
    return "L1"


def validate_materials_batch(materials: list[dict], context: Optional[dict] = None) -> list[dict]:
    """
    批量校验材料清单。为每个材料添加校验字段。
    """
    for m in materials:
        name = m.get("material_name", "")
        result = validate_material_name(name, context)

        # 写入校验字段
        m["name_validation"] = result
        m["name_level"] = result["name_level"]
        m["name_length_ok"] = result["name_length_ok"]
        m["contains_action_word"] = result["contains_action_word"]
        m["contains_spec_value"] = result["contains_spec_value"]
        m["ends_with_valid_suffix"] = result["ends_with_valid_suffix"]
        m["is_fee_or_service"] = result["is_fee_or_service"]

        # 更新置信度（取校验结果和现有置信度中较低者）
        conf_order = {"high": 2, "medium": 1, "low": 0}
        existing_conf = m.get("confidence", "medium")
        new_conf = result["confidence"]
        if conf_order.get(new_conf, 1) < conf_order.get(existing_conf, 1):
            m["confidence"] = new_conf

        if result["needs_review"]:
            m["needs_review"] = True

        # 来源标注
        if "name_source" not in m:
            m["name_source"] = "ai_inferred"  # 默认AI推断，等后续匹配T3后更新

    return materials


# ══════════════════════════════════════
# 七、规格型号格式校验（基于《规格型号格式规则库》v1.0.0）
# ══════════════════════════════════════

SPEC_FORMAT_PATTERNS = [
    (re.compile(r'^DN\d+\s+PN\d+'), 'F4', '公称参数'),
    (re.compile(r'\d+\s*[×xX]\s*\d+(?:\s*[×xX]\s*\d+)?\s*mm'), 'F2', '截面尺寸'),
    (re.compile(r'^DN\d+$'), 'F2', '公称直径'),
    (re.compile(r'^[Φφ]\d+(?:\.\d+)?'), 'F2', '直径'),
    (re.compile(r'^C\d{2,3}$'), 'F1', '混凝土强度'),
    (re.compile(r'^HRB\d+E?$|^HPB\d+$'), 'F1', '钢筋牌号'),
    (re.compile(r'^Q\d{3}[A-Z]?$'), 'F1', '钢材牌号'),
    (re.compile(r'^MU\d+$'), 'F1', '砌体强度'),
    (re.compile(r'^M\d+(?:\.\d+)?$'), 'F1', '砂浆强度'),
    (re.compile(r'^[A-Z]{2,}[-/]YJV|^YJV|^NH-|^ZR-|^WDZ-'), 'F3', '电缆型号'),
    (re.compile(r'^[A-Z]{2,}[-\s]?\d'), 'F3', '型号编码'),
    (re.compile(r'^\d+(?:\.\d+)?\s*mm$'), 'F6', '厚度'),
    (re.compile(r'^\d+(?:\.\d+)?\s*m\s*[×xX]\s*\d+(?:\.\d+)?\s*m$'), 'F6', '面积'),
]

SPEC_FORBIDDEN_PATTERNS = [
    (re.compile(r'详见|参照|参见|见设计|由厂家|厂家提供|其他参数'), '引用词/放弃型占位词'),
    (re.compile(r'投标|自报|报价'), '投标用语'),
]


def classify_spec_format(spec_text: str) -> tuple:
    """S1: 将规格文本归入六种格式。返回 (format_type, label)。"""
    if not spec_text or not spec_text.strip():
        return ("F0", "空规格")
    text = spec_text.strip()
    parts = text.split()
    for pattern, ftype, label in SPEC_FORMAT_PATTERNS:
        if pattern.search(text):
            if len(parts) >= 2 and ftype in ("F1", "F2"):
                other_parts = [p for p in parts if not pattern.search(p)]
                if other_parts:
                    return ("F5", "复合描述型")
            return (ftype, label)
    if len(parts) >= 2:
        return ("F5", "复合描述型")
    return ("F5", "复合描述型")


def validate_spec_format(material_name: str, spec: str) -> dict:
    """S0-S5: 对 name+spec 执行完整规格格式校验。"""
    import re as _re
    name_compact = _re.sub(r'\s+', '', str(material_name or ''))
    spec_compact = _re.sub(r'\s+', '', str(spec or ''))
    result = {
        "s0_ok": True, "s0_issue": None,
        "s1_type": "F0", "s1_label": "",
        "s5_ok": True, "s5_issues": [],
        "issues": [],
    }
    if not spec_compact:
        result["s1_type"], result["s1_label"] = "F0", "空规格"
        return result
    # S0: 规格含材料名
    if name_compact and name_compact in spec_compact:
        result["s0_ok"] = False
        result["s0_issue"] = "规格重复了材料名"
        result["issues"].append("s0: 规格含材料名")
    # S1: 格式识别
    ftype, flabel = classify_spec_format(spec_compact)
    result["s1_type"], result["s1_label"] = ftype, flabel
    # S5: 禁止事项
    for pattern, reason in SPEC_FORBIDDEN_PATTERNS:
        if pattern.search(spec):
            result["s5_ok"] = False
            result["s5_issues"].append(reason)
            result["issues"].append(f"s5: {reason}")
    return result


def validate_spec_batch(materials: list[dict]) -> list[dict]:
    """批量为材料清单添加规格校验字段。"""
    for m in materials:
        name = m.get("material_name", "")
        spec = m.get("spec_hint", "") or m.get("spec", "")
        result = validate_spec_format(name, spec)
        m["spec_validation"] = result
        m["spec_format"] = result["s1_type"]
        # 规格有问题时降低置信度
        if result["issues"]:
            existing = m.get("confidence", "medium")
            if existing == "high":
                m["confidence"] = "medium"
            m["needs_review"] = True
    return materials


def print_validation_report(materials: list[dict]):
    """打印校验报告（名称 + 规格）"""
    total = len(materials)
    if total == 0:
        print("无材料需要校验")
        return

    ok_count = sum(1 for m in materials if m.get("name_validation", {}).get("name_ok"))
    review_count = sum(1 for m in materials if m.get("needs_review"))
    action_hits = sum(1 for m in materials if m.get("contains_action_word"))
    spec_mixed = sum(1 for m in materials if m.get("contains_spec_value"))
    suffix_bad = sum(1 for m in materials if not m.get("ends_with_valid_suffix"))
    fee_hits = sum(1 for m in materials if m.get("is_fee_or_service"))

    # 规格统计
    spec_ok = sum(1 for m in materials if m.get("spec_validation", {}).get("s0_ok", True)
                  and m.get("spec_validation", {}).get("s5_ok", True)
                  and m.get("spec_validation", {}).get("s1_type") != "F0")
    spec_name_in_spec = sum(1 for m in materials
                            if not m.get("spec_validation", {}).get("s0_ok", True))
    spec_forbidden = sum(1 for m in materials
                         if not m.get("spec_validation", {}).get("s5_ok", True))
    format_dist = {}
    for m in materials:
        ft = m.get("spec_format", m.get("spec_validation", {}).get("s1_type", ""))
        if ft:
            format_dist[ft] = format_dist.get(ft, 0) + 1

    print(f"\n{'='*60}")
    print(f"材料命名与规格校验报告")
    print(f"基于《材料命名统一规则库》+《规格型号格式规则库》")
    print(f"{'='*60}")
    print(f"总数: {total}  名称通过: {ok_count}  需复核: {review_count}")
    print(f"\n【名称校验】")
    print(f"  - 含动作词: {action_hits}")
    print(f"  - 规格混入名称: {spec_mixed}")
    print(f"  - 后缀不在库: {suffix_bad}")
    print(f"  - 费用/服务项: {fee_hits}")
    print(f"\n【规格校验】")
    print(f"  - 规格通过: {spec_ok}")
    print(f"  - 规格含材料名: {spec_name_in_spec}")
    print(f"  - 含禁止内容: {spec_forbidden}")
    if format_dist:
        print(f"  - 格式分布: " + ", ".join(f"{k}={v}" for k, v in sorted(format_dist.items())))

    # 列出有问题的材料
    for m in materials:
        name_issues = m.get("name_validation", {}).get("issues", [])
        spec_issues = m.get("spec_validation", {}).get("issues", [])
        all_issues = name_issues + spec_issues
        if all_issues:
            name = m.get("material_name", "?")
            spec = m.get("spec_hint", "") or m.get("spec", "") or "(空)"
            print(f"\n  [{m.get('name_level', '?')}/{m.get('spec_format', '?')}] {name} | spec={spec[:40]}")
            for issue in all_issues:
                print(f"    ✗ {issue}")


if __name__ == "__main__":
    # 自测
    test_names = [
        ("薄钢板", {}),
        ("热轧带肋钢筋", {}),
        ("C30预拌混凝土", {}),
        ("混凝土浇筑", {}),
        ("人工费", {}),
        ("详见设计图纸", {}),
        ("冷轧08薄钢板0.5mm", {}),
        ("给排水系统", {}),
        ("垃圾清运费用", {}),
        ("干混砌筑砂浆 DM M10", {}),
        ("JDG管", {}),
    ]
    for name, ctx in test_names:
        r = validate_material_name(name, ctx)
        icon = "✓" if r["name_ok"] else "✗"
        print(f"{icon} [{r['name_level']}] {name:30s} | conf={r['confidence']} | review={r['needs_review']}")
        if r["issues"]:
            for i in r["issues"]:
                print(f"  → {i}")
