"""省份适配数据加载与匹配层。

数据源：标准知识库/源数据/省份适配/CSV导出/
- 06_province_config.csv         省份基础画像(气候/沿海/供暖/定额)
- 07_province_material_rules.csv  材料级规则(EXCLUDE/CONSUMPTION_ADJUST/COEFF_OVERRIDE/SPEC_ADJUST)
- 08_province_param_overrides.csv 参数级覆盖(按T2映射编码替换损耗率)
- 09_bj_t5_dimensions.csv         T5维度系数(按分部+条件替换消耗系数)
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from 配置 import KB_PROV_DATA as _KB_PROV_DATA


# ═══════════════════════════════
# Data Classes
# ═══════════════════════════════

@dataclass
class ProvinceProfile:
    """省份基础画像"""
    code: str           # 省份编码 SN/GD/BJ
    name: str           # 省份名称
    full_name: str      # 全称
    climate_zone: str   # 气候分区
    is_coastal: bool    # 是否沿海
    has_heating: bool   # 有无供暖
    is_baseline: bool   # 是否基准省份
    install_quota: str  # 安装定额名称
    install_year: str   # 安装定额年份
    build_quota: str    # 房建定额名称
    build_year: str     # 房建定额年份


@dataclass
class ProvinceRule:
    """省份材料级规则"""
    province_code: str      # 省份编码
    section_code: str       # 分部编码(空=全分部)
    material_id: str        # 物料ID(空=全物料)
    rule_type: str          # EXCLUDE/CONSUMPTION_ADJUST/COEFF_OVERRIDE/SPEC_ADJUST/REQUIRE_HIGHER_SPEC/INCLUDE_SUBCHAPTER/INCLUDE_HEATING
    adjust_coeff: str       # 调整系数
    reason: str             # 调整原因
    row_index: int          # 原始行号，用于同分排序


@dataclass
class ProvinceParamOverride:
    """省份参数级覆盖"""
    province_code: str      # 省份编码
    specialty_code: str     # 专业编码(01房建/03安装)
    t2_map_code: str        # T2映射编码
    param_name: str         # 参数名
    base_value: str         # 基准损耗率/系数
    override_value: str     # 覆盖损耗率/系数
    base_desc: str          # 基准值描述
    override_desc: str      # 覆盖值描述
    diff_tag: str           # 差异标签 MAJOR/MINOR/SAME
    quota_source: str       # 定额出处
    active: bool            # 是否激活


@dataclass
class ProvinceT5Dimension:
    """T5维度消耗系数"""
    province_code: str      # 省份编码 BJ
    specialty_code: str     # 专业编码
    map_code: str           # 映射编码
    section_code: str       # 分部编码
    dim_name: str           # 维度名称
    conditions: dict        # 条件JSON
    coefficient: float      # 系数
    num_unit: str           # 分子单位
    den_unit: str           # 分母单位
    includes_loss: bool     # 是否含损耗
    source: str             # 来源说明


# ═══════════════════════════════
# 缓存
# ═══════════════════════════════

_cache: dict[str, dict] = {}


def _read_csv(filename: str) -> list[dict]:
    path = _KB_PROV_DATA / filename
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _cache_key(province_code: str) -> str:
    return province_code.upper().strip()


# ═══════════════════════════════
# 加载函数
# ═══════════════════════════════

def load_province_config(province_code: str) -> Optional[ProvinceProfile]:
    """加载省份画像"""
    code = _cache_key(province_code)
    rows = _read_csv("06_province_config.csv")
    for r in rows:
        if r.get("省份编码", "").strip().upper() == code:
            return ProvinceProfile(
                code=r["省份编码"].strip(),
                name=r.get("省份名称", "").strip(),
                full_name=r.get("全称", "").strip(),
                climate_zone=r.get("气候分区", "").strip(),
                is_coastal=r.get("是否沿海", "0") == "1",
                has_heating=r.get("有无供暖", "1") == "1",
                is_baseline=r.get("是否基准", "0") == "1",
                install_quota=r.get("安装定额", "").strip(),
                install_year=r.get("安装定额年份", "").strip(),
                build_quota=r.get("房建定额", "").strip(),
                build_year=r.get("房建定额年份", "").strip(),
            )
    return None


def load_all_provinces() -> list[ProvinceProfile]:
    """加载全部省份配置"""
    rows = _read_csv("06_province_config.csv")
    profiles = []
    for r in rows:
        profiles.append(ProvinceProfile(
            code=r["省份编码"].strip(),
            name=r.get("省份名称", "").strip(),
            full_name=r.get("全称", "").strip(),
            climate_zone=r.get("气候分区", "").strip(),
            is_coastal=r.get("是否沿海", "0") == "1",
            has_heating=r.get("有无供暖", "1") == "1",
            is_baseline=r.get("是否基准", "0") == "1",
            install_quota=r.get("安装定额", "").strip(),
            install_year=r.get("安装定额年份", "").strip(),
            build_quota=r.get("房建定额", "").strip(),
            build_year=r.get("房建定额年份", "").strip(),
        ))
    return profiles


def load_material_rules(province_code: str) -> list[ProvinceRule]:
    """加载某省的材料级规则"""
    code = _cache_key(province_code)
    rows = _read_csv("07_province_material_rules.csv")
    rules = []
    for i, r in enumerate(rows):
        if r.get("省份编码", "").strip().upper() == code:
            rules.append(ProvinceRule(
                province_code=r["省份编码"].strip(),
                section_code=r.get("分部编码", "").strip(),
                material_id=r.get("物料ID", "").strip(),
                rule_type=r.get("规则类型", "").strip(),
                adjust_coeff=r.get("调整系数", "").strip(),
                reason=r.get("调整原因", "").strip(),
                row_index=i,
            ))
    return rules


def load_param_overrides(province_code: str) -> dict[tuple[str, str], ProvinceParamOverride]:
    """加载某省的参数级覆盖，返回 {(T2映射编码, 参数名): override}"""
    code = _cache_key(province_code)
    rows = _read_csv("08_province_param_overrides.csv")
    overrides = {}
    for r in rows:
        if r.get("省份编码", "").strip().upper() != code:
            continue
        if r.get("active_flag", "1") != "1":
            continue
        ov = ProvinceParamOverride(
            province_code=r["省份编码"].strip(),
            specialty_code=r.get("专业编码", "").strip(),
            t2_map_code=r.get("T2映射编码", "").strip(),
            param_name=r.get("参数名", "").strip(),
            base_value=r.get("基准损耗率", "").strip(),
            override_value=r.get("覆盖损耗率", "").strip(),
            base_desc=r.get("基准值描述", "").strip(),
            override_desc=r.get("覆盖值描述", "").strip(),
            diff_tag=r.get("差异标签", "").strip(),
            quota_source=r.get("定额出处", "").strip(),
            active=True,
        )
        key = (ov.t2_map_code, ov.param_name)
        # 如果同一个key已有覆盖，后面的覆盖前面的(按CSV行号)
        overrides[key] = ov
    return overrides


def load_t5_dimensions(province_code: str) -> list[ProvinceT5Dimension]:
    """加载某省的T5维度消耗系数"""
    code = _cache_key(province_code)
    rows = _read_csv("09_bj_t5_dimensions.csv")
    dims = []
    for r in rows:
        if r.get("省份编码", "").strip().upper() != code:
            continue
        cond_str = r.get("条件JSON", "{}").strip()
        try:
            conditions = json.loads(cond_str) if cond_str else {}
        except json.JSONDecodeError:
            conditions = {}
        try:
            coeff = float(r.get("系数", "1.0").strip())
        except ValueError:
            coeff = 1.0
        dims.append(ProvinceT5Dimension(
            province_code=r["省份编码"].strip(),
            specialty_code=r.get("专业编码", "").strip(),
            map_code=r.get("映射编码", "").strip(),
            section_code=r.get("分部编码", "").strip(),
            dim_name=r.get("维度名称", "").strip(),
            conditions=conditions,
            coefficient=coeff,
            num_unit=r.get("分子单位", "").strip(),
            den_unit=r.get("分母单位", "").strip(),
            includes_loss=r.get("是否含损耗", "0") == "1",
            source=r.get("来源说明", "").strip(),
        ))
    return dims


# ═══════════════════════════════
# 匹配函数
# ═══════════════════════════════

def match_material_rules(
    rules: list[ProvinceRule],
    material_id: str,
    section_code: str,
) -> list[ProvinceRule]:
    """匹配材料级规则，按评分降序排列。

    评分逻辑:
    - 物料ID精确匹配: +3分
    - 物料ID为空(全物料): +0分, 物料ID不匹配: 跳过
    - 分部编码精确匹配: +2分
    - 分部编码为空(全分部): +0分, 分部不匹配: 跳过
    - 同分按原始行号排序
    """
    mid = material_id.strip()
    sec = section_code.strip()

    scored = []
    for rule in rules:
        # 物料ID匹配
        if rule.material_id:
            if rule.material_id == mid:
                score = 3
            else:
                continue  # 有指定物料ID但不匹配，跳过
        else:
            score = 0  # 全物料匹配

        # 分部编码匹配
        if rule.section_code:
            if rule.section_code == sec:
                score += 2
            else:
                continue  # 有指定分部但不匹配，跳过
        # else: score += 0  全分部匹配

        scored.append((score, rule.row_index, rule))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [s[2] for s in scored]


def match_param_override(
    overrides: dict[tuple[str, str], ProvinceParamOverride],
    t2_map_code: str,
    param_name: str,
) -> Optional[ProvinceParamOverride]:
    """精确匹配参数覆盖"""
    return overrides.get((t2_map_code.strip(), param_name.strip()))


def match_t5_dimension(
    dimensions: list[ProvinceT5Dimension],
    section_code: str,
    material_attrs: dict,
) -> Optional[ProvinceT5Dimension]:
    """匹配T5维度系数。按分部编码筛选，按条件JSON匹配。"""
    sec = section_code.strip()
    candidates = [d for d in dimensions if d.section_code == sec or not d.section_code]

    for dim in candidates:
        if not dim.conditions:
            if dim.section_code == sec:
                return dim
            continue

        # 检查条件JSON的所有key-value是否都满足
        all_match = True
        for cond_key, cond_val in dim.conditions.items():
            mat_val = material_attrs.get(cond_key)
            if mat_val is None:
                all_match = False
                break
            # 支持范围条件: {"dn_min": 65, "dn_max": 200}
            if cond_key.endswith("_min"):
                base_key = cond_key[:-4]
                try:
                    if float(mat_val) < float(cond_val):
                        all_match = False
                        break
                except (ValueError, TypeError):
                    all_match = False
                    break
            elif cond_key.endswith("_max"):
                base_key = cond_key[:-4]
                try:
                    if float(mat_val) > float(cond_val):
                        all_match = False
                        break
                except (ValueError, TypeError):
                    all_match = False
                    break
            else:
                if str(mat_val).strip().lower() != str(cond_val).strip().lower():
                    all_match = False
                    break

        if all_match and dim.section_code == sec:
            return dim

    return None


# ═══════════════════════════════
# 批量加载(在run_breakdown中用)
# ═══════════════════════════════

@dataclass
class ProvinceContext:
    """省份完整上下文，在一次拆解中传递"""
    profile: Optional[ProvinceProfile]
    material_rules: list[ProvinceRule]
    param_overrides: dict[tuple[str, str], ProvinceParamOverride]
    t5_dimensions: list[ProvinceT5Dimension]

    @property
    def code(self) -> str:
        return self.profile.code if self.profile else "SN"

    @property
    def is_configured(self) -> bool:
        return self.profile is not None

    def describe_for_prompt(self) -> str:
        """生成3-5句话的AI上下文描述"""
        if not self.profile:
            return "项目位于陕西省(寒冷地区,有供暖,非沿海)。使用陕西省2025定额。"
        p = self.profile
        parts = [f"项目位于{p.full_name}({p.name})。"]
        parts.append(f"气候分区为{p.climate_zone}，"
                     f"{'沿海地区，需考虑盐雾腐蚀。' if p.is_coastal else '非沿海地区。'}"
                     f"{'有冬季集中供暖需求。' if p.has_heating else '无集中供暖需求。'}")
        parts.append(f"使用{p.install_quota}({p.install_year})作为计价依据。")

        if self.material_rules:
            excludes = [r for r in self.material_rules if r.rule_type == "EXCLUDE"]
            if excludes:
                reasons = set(r.reason for r in excludes)
                parts.append(f"注意：以下类型的材料不适用: {'; '.join(list(reasons)[:3])}。")
        return "".join(parts)


def load_province_context(province_code: str) -> ProvinceContext:
    """一键加载省份完整上下文"""
    code = _cache_key(province_code)
    profile = load_province_config(code)
    rules = load_material_rules(code)
    overrides = load_param_overrides(code)
    dims = load_t5_dimensions(code)
    return ProvinceContext(
        profile=profile,
        material_rules=rules,
        param_overrides=overrides,
        t5_dimensions=dims,
    )