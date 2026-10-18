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
"""
from __future__ import annotations

import re
from collections import defaultdict

BOX_COMPONENT_NAME = "配电箱体"
UNASSIGNED = "未归属"

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
    """件数只取整数：图纸/汇总里本来就是整件，浮点写法是聚合留下的尾巴。"""
    try:
        q = float(value or 0)
    except (TypeError, ValueError):
        return 1
    if q <= 0:
        return 1
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
    return result


def component_total(rows_by_box: dict[str, list[dict]]) -> int:
    """各柜件数合计（含未归属），用来核对 Σ各柜 == Σ components.quantity。"""
    return sum(int(row.get("quantity") or 0) for rows in rows_by_box.values() for row in rows)


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


if __name__ == "__main__":
    _self_check()
    print("box_components self-check ok")
