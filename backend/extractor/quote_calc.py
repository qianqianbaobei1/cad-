# -*- coding: utf-8 -*-
"""客户口径的唯一计费链。

口径来自客户正式报价单（制丝线升级改造项目），每个柜逐块复算一致：

    小计        = Σ 明细行单价 × 数量（不计价/报价不含的行按 0 计）
    辅料        = 小计 × 辅料率
    成套制作费  = (小计 + 辅料) × 成套制作费率
    税费        = (小计 + 辅料 + 成套制作费) × 税费率
    单台合计    = 小计 + 辅料 + 成套制作费 + 税费
    该柜总价    = 单台合计 × 台数
    项目合计    = Σ 各柜总价（2 位小数）
    项目总计    = 合计取整到元（大写金额按总计写）

屏幕组价与组价导出 Excel 都只调用本模块，费用在每一步按分四舍五入
（ROUND_HALF_UP），与客户报价单的逐块金额一致。费率默认值来自
config/delivery.json 的 quote_template，代码里不写死。
"""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Iterable, Mapping

from .config import delivery as _delivery

_CFG = _delivery().get("quote_template") or {}
_CENT = Decimal("0.01")
_YUAN = Decimal("1")

_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_UNITS = ("仟", "佰", "拾", "")
_GROUPS = ("", "万", "亿", "兆")


def default_rates() -> dict[str, float]:
    """客户口径默认费率：辅料 / 成套制作费 / 税费，全部读 config/delivery.json。"""
    return {
        "aux": float(_CFG["auxiliary_rate"]),
        "mfg": float(_CFG["manufacture_rate"]),
        "tax": float(_CFG["tax_rate"]),
    }


def resolve_rates(aux: float | None = None, mfg: float | None = None,
                  tax: float | None = None) -> dict[str, float]:
    """接口传了费率就用传入值，没传就回落到配置默认值。"""
    rates = default_rates()
    for key, value in (("aux", aux), ("mfg", mfg), ("tax", tax)):
        if value is not None:
            rates[key] = float(value)
    return rates


def _dec(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _round2(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _line_priced(line: Mapping[str, Any]) -> bool:
    for key in ("priced", "included", "quote_included"):
        if line.get(key) is False:
            return False
    return True


def _line_amount(line: Mapping[str, Any]) -> Decimal:
    if not _line_priced(line):
        return Decimal("0")
    qty = _dec(line.get("quantity", line.get("qty", 1)))
    price = _dec(line.get("unit_price", line.get("price", 0)))
    return _round2(price * qty)


def quote_box(lines: Iterable[Mapping[str, Any]], quantity: Any = 1,
              rates: Mapping[str, float] | None = None) -> dict[str, Any]:
    """一台柜的明细行 → 小计/辅料/成套制作费/税费/单台合计/柜总价。"""
    rate = default_rates()
    for key in rate:
        if rates and rates.get(key) is not None:
            rate[key] = float(rates[key])

    subtotal = _round2(sum((_line_amount(line) for line in lines), Decimal("0")))
    auxiliary = _round2(subtotal * _dec(rate["aux"]))
    manufacture = _round2((subtotal + auxiliary) * _dec(rate["mfg"]))
    tax = _round2((subtotal + auxiliary + manufacture) * _dec(rate["tax"]))
    unit_total = _round2(subtotal + auxiliary + manufacture + tax)
    qty = _dec(quantity if quantity not in (None, "") else 1)
    total = _round2(unit_total * qty)

    return {
        "quantity": float(qty),
        "subtotal": float(subtotal),
        "auxiliary": float(auxiliary),
        "manufacture": float(manufacture),
        "tax": float(tax),
        "unit_total": float(unit_total),
        "total": float(total),
        "rates": dict(rate),
    }


def _box_total(box: Any) -> Decimal:
    if isinstance(box, Mapping):
        value = box.get("total", box.get("total_price", box.get("subtotal", 0)))
    else:
        value = box
    return _dec(value)


def quote_project(boxes: Iterable[Any]) -> dict[str, Any]:
    """各柜结果 → 项目合计（2 位小数）、总计（取整到元）与大写金额。"""
    total = _round2(sum((_box_total(box) for box in boxes), Decimal("0")))
    grand_total = int(total.quantize(_YUAN, rounding=ROUND_HALF_UP))
    return {
        "total": float(total),
        "grand_total": grand_total,
        "upper": rmb_upper(grand_total),
    }


def _section(num: int) -> str:
    """0 < num < 10000 的四位读数，组内为零的位合并成一个“零”。"""
    out = ""
    pending_zero = False
    for val, unit in zip((1000, 100, 10, 1), _UNITS):
        d = num // val % 10
        if d == 0:
            pending_zero = bool(out)
        else:
            if pending_zero:
                out += "零"
                pending_zero = False
            out += _DIGITS[d] + unit
    return out


def rmb_upper(amount: Any) -> str:
    """人民币大写金额（支持到亿）。

    走代码算，不用 Excel 的 [DBNum2] 格式串：后者在 LibreOffice / 非中文区域
    设置里会直接显示成“/通用格式元”，报价单上出现这种字样等于没写。
    """
    fen_total = int(_round2(_dec(amount)) * 100)
    if fen_total == 0:
        return "零元整"
    sign = "负" if fen_total < 0 else ""
    fen_total = abs(fen_total)
    yuan, jiao, fen = fen_total // 100, fen_total // 10 % 10, fen_total % 10

    groups: list[int] = []
    left = yuan
    while left:
        groups.append(left % 10000)
        left //= 10000

    parts: list[str] = []
    for idx in range(len(groups) - 1, -1, -1):
        g = groups[idx]
        if g == 0:
            if parts and any(groups[:idx]) and not parts[-1].endswith("零"):
                parts.append("零")
            continue
        seg = _section(g)
        if idx < len(groups) - 1 and g < 1000 and parts and not parts[-1].endswith("零"):
            seg = "零" + seg
        parts.append(seg + _GROUPS[idx])

    text = ("".join(parts) if yuan else "零").rstrip("零") or "零"
    text += "元"

    if jiao == 0 and fen == 0:
        return sign + text + "整"
    if jiao:
        text += _DIGITS[jiao] + "角"
        if fen:
            text += _DIGITS[fen] + "分"
    elif fen:
        text += "零" + _DIGITS[fen] + "分"
    return sign + text
