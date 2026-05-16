"""
透明消耗量计算 — 三步分解：理论量 → 现场用量 → 采购量

三步公式:
  N_design = Q_boq × C_base          # 净用量（不含损耗）
  N_site   = N_design × (1 + R_eng)   # 现场用量（含施工损耗）
  N_proc   = N_site × (1 + R_proc)    # 采购量（含采购/运输损耗）

损耗率来源优先级:
  R_eng (施工损耗): T2规则 > 省份参数覆盖 > 品类树标准损耗率 > 材料类型默认
  R_proc (采购损耗): T2规则(如含) > 包装类型默认

用法:
  breakdown = calculate_consumption(
      boq_quantity=320, boq_unit="m²",
      material_name="块料(面层)", material_id="MAT-TIL-001",
      explicit_thickness=0.02, t2_rule=best_t2,
  )
"""
import math
from dataclasses import dataclass, field

# ══════════════════════════════════════
# 默认损耗率（按材料类型）
# ══════════════════════════════════════

DEFAULT_ENG_LOSS_BY_CATEGORY = {
    "散装材料": 0.02,   # 混凝土、砂浆
    "块料": 0.05,       # 瓷砖、石材
    "卷材": 0.08,       # 卷材、地毯
    "板材": 0.05,       # 木地板、橡塑板
    "粉料": 0.03,       # 水泥、填缝剂
    "液体": 0.03,       # 涂料、界面剂
    "型材": 0.03,       # 龙骨、踢脚线
    "成品件": 0.01,     # 设备、成品构件
}

DEFAULT_PROC_LOSS_BY_PACKAGE = {
    "袋装": 0.005,
    "散装/罐车": 0.01,
    "件装": 0.0,
    "卷": 0.005,
    "桶装": 0.005,
}

DEFAULT_LOSS_BY_MATERIAL_TYPE = {
    "混凝土": ("散装材料", "散装/罐车"),
    "砂浆": ("散装材料", "散装/罐车"),
    "预拌砂浆": ("散装材料", "袋装"),
    "瓷砖": ("块料", "件装"),
    "块料": ("块料", "件装"),
    "石材": ("块料", "件装"),
    "木地板": ("板材", "件装"),
    "橡塑": ("卷材", "卷"),
    "填缝剂": ("粉料", "袋装"),
    "界面剂": ("液体", "桶装"),
    "水泥": ("粉料", "袋装"),
    "龙骨": ("型材", "件装"),
    "踢脚线": ("型材", "件装"),
    "防潮膜": ("卷材", "卷"),
    "保护膜": ("卷材", "卷"),
    "钢筋": ("型材", "件装"),
    "钢筋网片": ("型材", "件装"),
}


@dataclass
class ConsumptionTrace:
    """单步计算痕迹"""
    step: str  # "base" / "eng_loss" / "proc_loss"
    formula: str
    input_value: float
    input_unit: str
    coefficient: float
    output_value: float
    output_unit: str
    source: str  # 系数来源
    notes: str


@dataclass
class ConsumptionBreakdown:
    """完整的三步消耗量分解"""
    boq_quantity: float
    boq_unit: str

    # Step 1: 基础含量
    base_coeff: float
    base_coeff_unit: str
    base_coeff_source: str
    design_quantity: float      # N_design

    # Step 2: 施工损耗
    eng_loss_rate: float
    eng_loss_source: str
    site_quantity: float         # N_site

    # Step 3: 采购损耗
    proc_loss_rate: float
    proc_loss_source: str
    procurement_quantity: float  # N_proc

    # 汇总
    total_coefficient: float     # C_total = C_base × (1+R_eng) × (1+R_proc)

    # 采购单位
    purchase_unit: str
    rounded_quantity: float      # 取整后的采购量

    # 追溯
    trace: list[ConsumptionTrace] = field(default_factory=list)
    confidence: str = "high"

    def describe(self) -> str:
        lines = [
            f"清单量: {self.boq_quantity} {self.boq_unit}",
            f"→ 净用量: {self.design_quantity:.3f} (系数 {self.base_coeff} {self.base_coeff_unit}, 来源: {self.base_coeff_source})",
            f"→ 现场用量: {self.site_quantity:.3f} (施工损耗 {self.eng_loss_rate:.1%}, 来源: {self.eng_loss_source})",
            f"→ 采购量: {self.procurement_quantity:.3f} (采购损耗 {self.proc_loss_rate:.1%}, 来源: {self.proc_loss_source})",
            f"总系数: {self.total_coefficient:.4f}, 取整采购量: {self.rounded_quantity} {self.purchase_unit}",
        ]
        return "\n".join(lines)


def calculate_consumption(
    boq_quantity: float,
    boq_unit: str,
    material_name: str = "",
    material_id: str = "",
    explicit_thickness: float | None = None,
    t2_rule: dict | None = None,
    cat_loss_rate: float | None = None,
    province_loss_rate: float | None = None,
    province_loss_source: str = "",
    explicit_length: float | None = None,
) -> ConsumptionBreakdown:
    """三步消耗量计算。

    Args:
        boq_quantity: 清单工程量
        boq_unit: 清单单位
        material_name: 材料名称
        material_id: T3物料ID
        explicit_thickness: 显式厚度(m), 从项目特征提取
        t2_rule: T2规则行 (dict with 消耗量公式, 损耗率, 采购单位等)
        cat_loss_rate: 品类树标准损耗率
        province_loss_rate: 省份损耗率覆盖值
        province_loss_source: 省份损耗率来源说明
        explicit_length: 显式长度(m), 从项目特征提取(踢脚线等)
    """
    trace: list[ConsumptionTrace] = []

    # ── Step 1: 基础含量系数 C_base ──
    base_coeff = 1.0
    base_coeff_unit = f"{boq_unit}/{boq_unit}"
    base_coeff_source = "默认1:1"

    if t2_rule:
        formula = t2_rule.get("消耗量公式", "").strip()
        coeff_field = t2_rule.get("系数", "").strip()
        if coeff_field:
            try:
                base_coeff = float(coeff_field)
                base_coeff_source = f"T2规则系数 (映射编码: {t2_rule.get('映射编码', '')})"
            except (ValueError, TypeError):
                pass
        elif formula:
            # 尝试从公式中提取系数
            import re
            fm = re.match(r"(\d+\.?\d*)", formula)
            if fm:
                base_coeff = float(fm.group(1))
                base_coeff_source = f"T2规则公式: {formula}"

    # 有显式厚度时按体积换算
    if explicit_thickness and explicit_thickness > 0:
        # 面积 → 体积: C_base = thickness (m³/m²)
        if boq_unit in ("m²", "m2", "㎡"):
            volume_coeff = explicit_thickness  # m³/m²
            # 仅当没有T2规则覆盖时使用厚度推导
            if not t2_rule or not (t2_rule.get("系数") or t2_rule.get("消耗量公式")):
                base_coeff = volume_coeff
                base_coeff_unit = f"m³/m²"
                base_coeff_source = f"特征厚度推导: {explicit_thickness*1000:.0f}mm"

    # 踢脚线长度计算
    if explicit_length and explicit_length > 0:
        if not t2_rule:
            base_coeff = explicit_length
            base_coeff_unit = f"m/{boq_unit}"

    design_qty = boq_quantity * base_coeff

    trace.append(ConsumptionTrace(
        step="base", formula="Q × C_base",
        input_value=boq_quantity, input_unit=boq_unit,
        coefficient=base_coeff, output_value=design_qty,
        output_unit=boq_unit, source=base_coeff_source,
        notes="",
    ))

    # ── Step 2: 施工损耗率 R_eng ──
    eng_loss = 0.0
    eng_loss_source = "AI估算"

    # 来源优先级: T2 > 省份 > 品类树 > 材料类型默认
    if t2_rule:
        t2_loss = t2_rule.get("损耗率", "").strip()
        if t2_loss:
            try:
                eng_loss = float(t2_loss)
                eng_loss_source = f"T2规则 (映射编码: {t2_rule.get('映射编码', '')})"
            except (ValueError, TypeError):
                pass
    if province_loss_rate is not None and province_loss_source:
        eng_loss = province_loss_rate
        eng_loss_source = province_loss_source
    elif eng_loss == 0.0 and cat_loss_rate is not None:
        eng_loss = cat_loss_rate
        eng_loss_source = "品类树标准损耗率"
    elif eng_loss == 0.0:
        # 按材料类型推断默认损耗率
        for kw, (eng_cat, _) in DEFAULT_LOSS_BY_MATERIAL_TYPE.items():
            if kw in material_name:
                eng_loss = DEFAULT_ENG_LOSS_BY_CATEGORY.get(eng_cat, 0.02)
                eng_loss_source = f"默认{eng_cat}损耗率"
                break
        if eng_loss == 0.0:
            eng_loss = 0.02
            eng_loss_source = "默认通用损耗率"

    site_qty = design_qty * (1.0 + eng_loss)

    trace.append(ConsumptionTrace(
        step="eng_loss", formula="N_design × (1 + R_eng)",
        input_value=design_qty, input_unit=boq_unit,
        coefficient=1.0 + eng_loss, output_value=site_qty,
        output_unit=boq_unit, source=eng_loss_source,
        notes=f"R_eng = {eng_loss:.4f} ({eng_loss:.1%})",
    ))

    # ── Step 3: 采购/运输损耗率 R_proc ──
    proc_loss = 0.0
    proc_loss_source = "无采购损耗"

    # 从T2规则中检查是否有单独的采购损耗字段
    if t2_rule:
        t2_proc = t2_rule.get("采购损耗率", "").strip() or t2_rule.get("运输损耗率", "").strip()
        if t2_proc:
            try:
                proc_loss = float(t2_proc)
                proc_loss_source = "T2规则采购损耗"
            except (ValueError, TypeError):
                pass

    if proc_loss == 0.0:
        # 按包装类型推断
        for kw, (_, pkg_cat) in DEFAULT_LOSS_BY_MATERIAL_TYPE.items():
            if kw in material_name:
                proc_loss = DEFAULT_PROC_LOSS_BY_PACKAGE.get(pkg_cat, 0.0)
                if proc_loss > 0:
                    proc_loss_source = f"默认{pkg_cat}损耗"
                break

    proc_qty = site_qty * (1.0 + proc_loss)

    trace.append(ConsumptionTrace(
        step="proc_loss", formula="N_site × (1 + R_proc)",
        input_value=site_qty, input_unit=boq_unit,
        coefficient=1.0 + proc_loss, output_value=proc_qty,
        output_unit=boq_unit, source=proc_loss_source,
        notes=f"R_proc = {proc_loss:.4f} ({proc_loss:.1%})",
    ))

    # ── 汇总 ──
    total_coeff = base_coeff * (1.0 + eng_loss) * (1.0 + proc_loss)
    purchase_unit = boq_unit
    if t2_rule:
        t2_unit = t2_rule.get("采购单位", "").strip() or t2_rule.get("unit", "").strip()
        if t2_unit:
            purchase_unit = t2_unit

    # 取整（向上取整到合理精度）
    rounded_qty = _round_procurement(proc_qty, purchase_unit)

    # 置信度
    conf = "high"
    if eng_loss_source.startswith("默认"):
        conf = "medium"
    if base_coeff_source.startswith("默认"):
        conf = "medium"

    return ConsumptionBreakdown(
        boq_quantity=boq_quantity,
        boq_unit=boq_unit,
        base_coeff=round(base_coeff, 6),
        base_coeff_unit=base_coeff_unit,
        base_coeff_source=base_coeff_source,
        design_quantity=round(design_qty, 6),
        eng_loss_rate=round(eng_loss, 6),
        eng_loss_source=eng_loss_source,
        site_quantity=round(site_qty, 6),
        proc_loss_rate=round(proc_loss, 6),
        proc_loss_source=proc_loss_source,
        procurement_quantity=round(proc_qty, 6),
        total_coefficient=round(total_coeff, 6),
        purchase_unit=purchase_unit,
        rounded_quantity=rounded_qty,
        trace=trace,
        confidence=conf,
    )


def _round_procurement(quantity: float, unit: str) -> float:
    """按采购单位取整。

    - 散装材料(t, m³): 保留3位小数
    - 袋装材料(kg): 保留1位小数
    - 件装材料(个,套,台,樘): 向上取整到整数
    - 长度(m): 保留1位小数
    - 面积(m²): 保留2位小数
    """
    if unit in ("个", "套", "台", "樘", "件", "箱", "捆", "卷"):
        return float(math.ceil(quantity))
    elif unit in ("t", "m³", "m3"):
        return round(quantity, 3)
    elif unit in ("m",):
        return round(quantity, 1)
    elif unit in ("kg",):
        return round(quantity, 1)
    elif unit in ("m²", "m2", "㎡"):
        return round(quantity, 2)
    else:
        return round(quantity, 3)


# ══════════════════════════════════════
# 流水线集成辅助
# ══════════════════════════════════════

def breakdown_to_item_fields(breakdown: ConsumptionBreakdown) -> dict:
    """将ConsumptionBreakdown转为item dict可用的字段。"""
    return {
        "consumption_breakdown": {
            "design_quantity": breakdown.design_quantity,
            "eng_loss_rate": breakdown.eng_loss_rate,
            "eng_loss_source": breakdown.eng_loss_source,
            "site_quantity": breakdown.site_quantity,
            "proc_loss_rate": breakdown.proc_loss_rate,
            "proc_loss_source": breakdown.proc_loss_source,
            "procurement_quantity": breakdown.procurement_quantity,
            "total_coefficient": breakdown.total_coefficient,
            "trace": [
                {
                    "step": t.step,
                    "coefficient": t.coefficient,
                    "output_value": t.output_value,
                    "source": t.source,
                }
                for t in breakdown.trace
            ],
        },
        # 向后兼容字段
        "coefficient": round(breakdown.total_coefficient, 4),
        "suggested_quantity": breakdown.rounded_quantity,
        "unit": breakdown.purchase_unit,
    }
