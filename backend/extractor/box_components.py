"""按柜拆分项目级元器件汇总：屏幕组价、组价导出、拆量页共用这一份结果。

data["components"] 是**项目级**汇总：一行一个规格，quantity 是全项目总数，
used_in 列出这一规格用在哪些柜/回路（"02AP1 D01、02AP1 D02"）。只判断
"used_in 里有没有本柜"就把整行数量交给本柜，会把数量成倍放大；前端再自己
算一套，就更对不上。所以每柜元器件数量只在这里拆一次。

拆分顺序：
1. used_in 按 、,，;；换行 切段；段等于柜号、或以"柜号 "/"柜号-"开头，归该柜。
2. 片段数 == quantity：一段一件。
3. 片段数与 quantity 不等：先看回路/独立设备逐条明细能不能数出各柜真实件数
   （与 assemble 同一套器件解析），能对上就用它；对不上才按片段数比例分摊。
   分摊用最大余数取整，Σ各柜 == 该行 quantity，不出现小数件。
4. 一段都归不到任何柜的行整行进"未归属"，数量绝不摊到别的柜，并在 note 里
   标存疑（拆量页待核对页签按 note 含"存疑"收集）。
5. 数量为空或 0 的行不计件，留一行 0 件并标"数量未标注"，不凭空记 1 件。
6. 箱内一个断路器元器件都没有、回路里却有断路器规格的柜，在这里按回路补出
   断路器行（标"由回路图提取"）。组价不再自己合成，拆量页与组价永远同一份。
"""
from __future__ import annotations

import re
from collections import defaultdict

BOX_COMPONENT_NAME = "配电箱体"
UNASSIGNED = "未归属"
DOUBT_MARK = "存疑"
# 名里有这些词就算该柜已有断路器元器件，不再按回路补行
BREAKER_NAME_MARKS = ("断路器", "开关", "微断")
EMPTY_SPECS = ("", "-", "待确认")
SYNTHESIZED_NOTE = "由回路图提取（元器件汇总里没有该柜断路器，按回路逐条补出）"

_FRAGMENT_SPLIT = re.compile(r"[、,，;；\n]+")


def split_fragments(used_in) -> list[str]:
    """used_in 切成片段：中英文逗号/顿号/分号/换行都算分隔。"""
    return [t.strip() for t in _FRAGMENT_SPLIT.split(str(used_in or "")) if t.strip()]


def _box_codes(data: dict) -> tuple[str, ...]:
    """已声明的柜号，长柜号优先，避免 02AP1 抢走 02AP01 的片段。"""
    codes = {
        str(b.get("code") or b.get("box_code") or "").strip()
        for b in (data.get("boxes") or [])
        if str(b.get("code") or b.get("box_code") or "").strip()
    }
    return tuple(sorted(codes, key=lambda c: (-len(c), c)))


def match_box(fragment: str, codes: tuple[str, ...]) -> str | None:
    for code in codes:
        if fragment == code or fragment.startswith(f"{code} ") or fragment.startswith(f"{code}-"):
            return code
    return None


def _as_count(value) -> int:
    """件数只取整数：图纸/汇总里本来就是整件，浮点写法是聚合留下的尾巴。

    数量缺失或为 0 时返回 0（不计件），由调用方记一条“数量未标注”待核对；
    绝不把没写数量的行当成 1 件凭空计入。
    """
    try:
        q = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if q <= 0:
        return 0
    return int(q) if q == int(q) else int(round(q))


def _device_counts_by_spec(data: dict, codes: tuple[str, ...]) -> dict[str, dict[str, int]]:
    """规格 -> {柜号: 件数}，来自回路逐条器件与独立设备清单的明细条数。"""
    from .assemble import _parse_devices

    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for circuit in data.get("circuits") or []:
        box = str(circuit.get("box") or circuit.get("box_code") or "").strip()
        if not box:
            continue
        for field in ("breaker", "contactor", "ct", "thermal"):
            devices = _parse_devices(str(circuit.get(field) or ""), allow_combo=field == "breaker")
            for spec, number in devices or []:
                counts[spec.strip()][box] += number
    for device in data.get("extra_devices") or []:
        spec = str(device.get("spec") or "").strip()
        if not spec:
            continue
        for fragment in split_fragments(device.get("used_in")):
            box = match_box(fragment, codes)
            if box:
                counts[spec][box] += _as_count(device.get("quantity"))
                break
    return {spec: dict(by_box) for spec, by_box in counts.items()}


def _allocate(quantity: int, counts: list[tuple[str, int]]) -> dict[str, int]:
    """按各柜片段数把整数总量分完：Σ == quantity，最大余数取整，不出现小数件。"""
    total = sum(c for _, c in counts)
    alloc = {code: (quantity * c) // total for code, c in counts}
    left = quantity - sum(alloc.values())
    if left > 0:
        # 余数大的先补 1 件；余数相同按片段多的、再按出现顺序（sort 稳定）
        ranked = sorted(counts, key=lambda kv: (-((quantity * kv[1]) % total), -kv[1]))
        for code, _ in ranked[:left]:
            alloc[code] += 1
    return alloc


def _row(original: dict, name: str, quantity: int, fragments: list[str],
         where: str, note: str) -> dict:
    row = dict(original or {})
    row["name"] = name
    row["quantity"] = quantity
    row["used_in"] = "、".join(fragments)
    row["where"] = where
    extra = str(note or "").strip()
    base = str((original or {}).get("note") or "").strip()
    row["note"] = "；".join(part for part in (base, extra) if part)
    return row


def _allocate_for_row(quantity: int, counts: list[tuple[str, int]], spec: str,
                      device_counts: dict[str, dict[str, int]]) -> tuple[dict[str, int], str]:
    total = sum(c for _, c in counts)
    truth = device_counts.get((spec or "").strip())
    if quantity != total and truth:
        if all(truth.get(code, 0) > 0 for code, _ in counts) and \
                sum(truth[code] for code, _ in counts) == quantity:
            # 回路/设备逐条明细能数平：这才是各柜真件数，比分片比更准
            return {code: truth[code] for code, _ in counts}, ""
    if quantity == total:
        return {code: c for code, c in counts}, ""
    alloc = _allocate(quantity, counts)
    detail = "、".join(f"{code} {number} 件" for code, number in alloc.items())
    note = f"全项目 {quantity} 件、used_in 只记 {total} 段，按片段数比例拆到各柜：{detail}"
    if quantity % total:
        note += "（除不尽，按最大余数取整，请核对）（存疑）"
    return alloc, note


def _has_breaker_row(rows: list[dict]) -> bool:
    return any(any(mark in str(row.get("name") or "") for mark in BREAKER_NAME_MARKS)
               for row in rows)


def _breaker_rows_from_circuits(data: dict, box_code: str) -> list[dict]:
    """柜里没有断路器元器件时，按回路逐条补出断路器行（控制/信号电缆回路不出）。"""
    from .assemble import is_control_cable_circuit

    rows: list[dict] = []
    for circuit in data.get("circuits") or []:
        box = str(circuit.get("box") or circuit.get("box_code") or "").strip()
        if box != box_code:
            continue
        spec = str(circuit.get("breaker") or circuit.get("breaker_spec") or "").strip()
        if spec in EMPTY_SPECS or is_control_cable_circuit(circuit):
            continue
        no = str(circuit.get("circuit_no") or "").strip()
        load = str(circuit.get("load_name") or "").strip()
        is_incoming = circuit.get("circuit_type") == "incoming" or "进线" in no or "进线" in load
        rows.append({
            "name": "进线断路器" if is_incoming else "分支断路器",
            "spec": spec,
            "unit": "只",
            "category": "",
            "quantity": 1,
            "used_in": f"{box_code} {no}".strip(),
            "where": no or "回路",
            "note": SYNTHESIZED_NOTE,
        })
    return rows


def split_components_by_box(data: dict | None) -> dict[str, list[dict]]:
    """返回 {柜号: [该柜元器件行]}；"未归属"键装一段都归不到柜的行。

    每行保留原 name/spec/unit/category 等字段，quantity 换成该柜件数，
    used_in/where 换成归到该柜的片段，note 追加拆分说明（存疑项进待核对）。
    """
    data = data or {}
    codes = _box_codes(data)
    device_counts = _device_counts_by_spec(data, codes)
    result: dict[str, list[dict]] = {}

    for original in data.get("components") or []:
        name = str(original.get("name") or "").strip() or "元器件"
        if name == BOX_COMPONENT_NAME:
            continue  # 箱体由壳体定额单独计价，不进元器件明细

        quantity = _as_count(original.get("quantity"))
        raw_used = original.get("used_in") or original.get("box") or original.get("box_code")
        fragments = split_fragments(raw_used)

        grouped: dict[str, list[str]] = {}
        for fragment in fragments:
            box = match_box(fragment, codes)
            if box:
                grouped.setdefault(box, []).append(fragment)

        if not grouped:
            result.setdefault(UNASSIGNED, []).append(_row(
                original, name, quantity, fragments, UNASSIGNED,
                "used_in 无法归属任何箱柜，数量未分摊，请人工指定箱柜（存疑）",
            ))
            continue

        if not quantity:
            # 数量没写就不许凭空记 1 件：留一行 0 件等人补数量，别让它悄悄消失
            box = next(iter(grouped))
            frags = grouped[box]
            refs = [frag[len(box):].strip(" -—") or frag for frag in frags]
            result.setdefault(box, []).append(_row(
                original, name, 0, frags, "、".join(refs),
                "数量未标注（原表为空或 0），未计件，请补数量后再核对（存疑）",
            ))
            continue

        counts = [(box, len(frags)) for box, frags in grouped.items()]
        alloc, note = _allocate_for_row(quantity, counts, str(original.get("spec") or ""), device_counts)
        for box, number in alloc.items():
            if number <= 0:
                continue
            frags = grouped[box]
            refs = [frag[len(box):].strip(" -—") or frag for frag in frags]
            result.setdefault(box, []).append(
                _row(original, name, number, frags, "、".join(refs), note)
            )
    # 箱里一个断路器元器件都没有、回路却有断路器规格：在这里补行，组价不再自己合成。
    for code in codes:
        rows = result.get(code) or []
        if _has_breaker_row(rows):
            continue
        synthesized = _breaker_rows_from_circuits(data, code)
        if synthesized:
            result[code] = rows + synthesized
    return result


def component_total(rows_by_box: dict[str, list[dict]]) -> int:
    """各柜件数合计（含未归属），用来核对 Σ各柜 == Σ components.quantity。"""
    return sum(int(row.get("quantity") or 0) for rows in rows_by_box.values() for row in rows)


def pending_checks(rows_by_box: dict[str, list[dict]]) -> list[dict]:
    """拆分结果 → 派生待核对清单（只随接口返回，不写回 job.json）。

    两类：note 带“存疑”的行，和一段都归不到柜的“未归属”行。
    """
    checks: list[dict] = []
    for code, rows in (rows_by_box or {}).items():
        unassigned = code == UNASSIGNED
        for row in rows:
            doubt = DOUBT_MARK in str(row.get("note") or "")
            if not (unassigned or doubt):
                continue
            checks.append({
                "box_code": "" if unassigned else code,
                "name": str(row.get("name") or "元器件"),
                "spec": str(row.get("spec") or ""),
                "unit": str(row.get("unit") or "只"),
                "quantity": int(row.get("quantity") or 0),
                "kind": "unassigned" if unassigned else "doubt",
                "where": str(row.get("where") or ""),
                "note": str(row.get("note") or ""),
            })
    return checks


def _self_check() -> None:
    """一条命令能跑的最小校验：多柜分摊、片段=数量、未归属、Σ守恒。"""
    data = {
        "boxes": [{"code": "1AP1"}, {"code": "1AP2"}, {"code": "1AP3"}],
        "circuits": [],
        "components": [
            {"name": "塑壳断路器", "spec": "MCCB-100/50A/3P", "unit": "只", "quantity": 6,
             "used_in": "1AP1 D01、1AP1 D02、1AP2 D01、1AP2 D02、1AP3 D01、1AP3 D02"},
            {"name": "电压表", "spec": "电压表", "unit": "只", "quantity": 3,
             "used_in": "1AP1、1AP2、1AP3"},
            {"name": "无归属件", "spec": "X-1", "unit": "只", "quantity": 2, "used_in": "无此柜"},
            {"name": "配电箱体", "spec": "1AP1 GGD", "unit": "台", "quantity": 1, "used_in": "1AP1"},
        ],
    }
    split = split_components_by_box(data)
    for code in ("1AP1", "1AP2", "1AP3"):
        assert len(split[code]) == 2, split[code]
        assert split[code][0]["quantity"] == 2, split[code][0]
        assert split[code][1]["quantity"] == 1, split[code][1]
    assert split[UNASSIGNED][0]["quantity"] == 2
    # 配电箱体不进元器件明细；其余 Σ各柜（含未归属）== 6 + 3 + 2
    assert component_total(split) == 11, component_total(split)

    # 数量未标注：不计件、留 0 件并标存疑
    missing = {
        "boxes": [{"code": "1AP1"}],
        "circuits": [],
        "components": [{"name": "电压表", "spec": "电压表", "unit": "只", "quantity": 0,
                        "used_in": "1AP1"}],
    }
    rows = split_components_by_box(missing)["1AP1"]
    assert [r["quantity"] for r in rows] == [0], rows
    assert DOUBT_MARK in rows[0]["note"]

    # 没有断路器元器件的柜：按回路补出断路器行，控制电缆回路不补
    synth = {
        "boxes": [{"code": "02AP1"}],
        "circuits": [
            {"box": "02AP1", "circuit_no": "D01", "breaker": "MCCB-160MA/50A/3P"},
            {"box": "02AP1", "circuit_no": "D01-C1", "cable": "ZR-KYJVP-0.6/1KV-10x1.5-SC32-CT",
             "load_name": "至现场操作柱"},
        ],
        "components": [],
    }
    rows = split_components_by_box(synth)["02AP1"]
    assert [(r["name"], r["spec"], r["quantity"]) for r in rows] == [
        ("分支断路器", "MCCB-160MA/50A/3P", 1)], rows
    assert component_total(split_components_by_box(synth)) == 1


if __name__ == "__main__":
    _self_check()
    print("box_components self-check ok")
