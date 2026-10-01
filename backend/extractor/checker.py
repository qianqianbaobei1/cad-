"""Conservative checks for contradictions in an extracted quotation list.

Warnings ask for review; they never rewrite values read from the drawing.
"""
from collections import defaultdict
from math import isfinite
import re

from .assemble import PREFIXES, _parse_devices, is_incoming_circuit
from .schema import ExtractionResult

# 回路 breaker 字段能汇总出的元器件名称，必须与 assemble 保持一致，否则一改分类
# 交叉核对就会把已存在的器件误判为缺失（例如隔离开关）
BREAKER_CATEGORIES = {name for _, name in PREFIXES} | {"断路器（其他）"}


def _spec(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def _is_valid_phase(phase: str) -> bool:
    """电气相序合法性判定：兼容工程常用的单相/三相全套国标标注格式。"""
    if not phase:
        return True
    p = re.sub(r"[\s,./~_、\-]+", "", phase).upper()
    valid_patterns = {
        "L1", "L2", "L3", "L1NPE", "L2NPE", "L3NPE",
        "L123", "L1L2L3", "L1L2L3PE", "L1L2L3NPE", "L13NPE", "L13PE",
        "L1L3NPE", "L1L3PE", "3P", "1P", "2P", "4P", "三相", "单相", "A", "B", "C", "ABC",
    }
    if p in valid_patterns:
        return True
    if re.match(r"^L[123](N)?(PE)?$", p):
        return True
    if re.match(r"^L1[~-]?L?3(N)?(PE)?$", p):
        return True
    if re.match(r"^L[1-3]{1,3}(N)?(PE)?$", p):
        return True
    return False


def check_result(result: ExtractionResult) -> list[str]:
    warnings: list[str] = []
    boxes = {box.code.strip(): box for box in result.boxes if box.code.strip()}

    for box in result.boxes:
        if box.quantity <= 0:
            warnings.append(f"箱体 {box.code or '(未编号)'} 数量为 {box.quantity}，请核对")

    for index, circuit in enumerate(result.circuits, 1):
        code = circuit.box.strip()
        if code and code not in boxes:
            warnings.append(f"第 {index} 条回路引用箱体 {code}，箱体清单中未找到，请核对")
        if circuit.phase and not _is_valid_phase(circuit.phase):
            warnings.append(f"第 {index} 条回路相序“{circuit.phase}”不在常见值中，请对照图纸核对")

    for index, component in enumerate(result.components, 1):
        if not isfinite(component.quantity) or component.quantity <= 0:
            warnings.append(f"第 {index} 项元器件 {component.name or component.spec or '(未命名)'} 数量无效，请核对")
        if component.unit and component.unit not in {"只", "台", "套", "米", "块", "个", "组"}:
            warnings.append(f"第 {index} 项元器件单位“{component.unit}”需对照图纸核对")

    # Compare only breaker expressions the assembler can parse safely.
    expected: dict[str, int] = defaultdict(int)
    for circuit in result.circuits:
        devices = _parse_devices(circuit.breaker, allow_combo=True)
        if not devices:
            continue
        box = boxes.get(circuit.box.strip())
        if box is None or box.quantity <= 0:
            continue
        for spec, count in devices:
            expected[_spec(spec)] += count * box.quantity

    actual: dict[str, float] = defaultdict(float)
    for component in result.components:
        if component.name in BREAKER_CATEGORIES and component.spec.strip():
            actual[_spec(component.spec)] += component.quantity

    for spec, count in expected.items():
        if spec not in actual:
            warnings.append(f"断路器 {spec}：回路逐条计数 {count} 只，元器件汇总未找到同规格项，请核对")
        elif actual[spec] != count:
            warnings.append(f"断路器 {spec}：回路逐条计数 {count} 只，元器件汇总 {actual[spec]:g} 只，请核对")

    # 三相负荷平衡度校验（国标限值 15%）
    phase_loads = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
    has_loads = False
    for c in result.circuits:
        if not c.power_kw:
            continue
        try:
            val_match = re.search(r"(\d+(?:\.\d+)?)", c.power_kw)
            if not val_match:
                continue
            val = float(val_match.group(1))
            p = (c.phase or "").upper().strip()
            if p == "L1":
                phase_loads["L1"] += val
                has_loads = True
            elif p == "L2":
                phase_loads["L2"] += val
                has_loads = True
            elif p == "L3":
                phase_loads["L3"] += val
                has_loads = True
            elif p in ("L123", "3P", "3PH", "L1,L2,L3"):
                phase_loads["L1"] += val / 3.0
                phase_loads["L2"] += val / 3.0
                phase_loads["L3"] += val / 3.0
                has_loads = True
        except ValueError:
            pass

    if has_loads:
        p_vals = [phase_loads["L1"], phase_loads["L2"], phase_loads["L3"]]
        p_max, p_min = max(p_vals), min(p_vals)
        if p_max > 1.0 and (phase_loads["L1"] > 0 and phase_loads["L2"] > 0 and phase_loads["L3"] > 0):
            unbalance = ((p_max - p_min) / p_max) * 100.0
            if unbalance > 15.0:
                warnings.append(
                    f"三相负荷平衡核验：L1={phase_loads['L1']:.1f}kW, L2={phase_loads['L2']:.1f}kW, L3={phase_loads['L3']:.1f}kW，"
                    f"三相负荷不平衡度达 {unbalance:.1f}%（超出国标15%限值），存在偏载风险，请核对配电分配"
                )

    # 进出线开关级配防越级跳闸核验
    def _extract_amp(spec_str: str) -> float | None:
        m = re.search(r"(?:/|/C|/D|\s)([1-9]\d{0,3})A?(?:/|$|\s)", spec_str, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return None

    incoming_amps = []
    outgoing_circuits = []
    for c in result.circuits:
        breaker_spec = c.breaker or ""
        amp = _extract_amp(breaker_spec)
        # 进线判定与 assemble 共用 is_incoming_circuit（原先这里还看 note 里的
        # "总开"/"进线"自由文本，与排序口径不一致，已统一）
        is_incoming = is_incoming_circuit(c)
        if is_incoming and amp:
            incoming_amps.append(amp)
        elif amp and not is_incoming:
            outgoing_circuits.append((c.circuit_no or "出线回路", breaker_spec, amp))

    if incoming_amps:
        min_incoming = min(incoming_amps)
        for c_no, spec, amp in outgoing_circuits:
            if amp > min_incoming:
                warnings.append(
                    f"开关级配核验：出线 {c_no} 额定电流 {amp:g}A（{spec}）大于进线主开关 {min_incoming:g}A，存在越级跳闸风险，请核对图纸"
                )

    return warnings
