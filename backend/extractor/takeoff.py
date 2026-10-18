# -*- coding: utf-8 -*-
"""原生读图引擎：从 DWG/DXF 直接读出箱柜、回路、器件（不经视觉模型）。

思路同 CAD快速看图——先认对象，再认版式：
1. 图幅：sheets.py 按真实图框分幅，只解析系统图 / 原理图（目录、说明、平面图不进）。
2. 每张系统图独立解析，互不串扰：
   - 开关电器规格文字（MCCB-…、RCBO/…、GL-…）是回路行的锚点：每条回路必有且只有一个；
   - 锚点按左对齐聚成列，箱柜顶部抬头（GGD / XXM / IP54 / 落地式）把一列切成一台台箱柜，
     没有抬头时按行距突变切分；
   - 箱柜左侧单独的那个开关是进线开关；进线区里的浪涌保护器、多功能表、后备保护器、
     进线互感器记为附加器件；
   - 每行的范围是相邻两行的中线，行内按词法取回路号、相序、电缆、负荷、功率、互感器、
     接触器、热继电器；
   - 箱名（带箱号的文字）按距离一对一配给箱柜，写着“配电箱 / 柜”的优先。
3. 表格式系统图（GGD 开关柜一列一柜）交给 gtable 按网格解析。
所有距离以本图文字字高为单位，词表在 config/pipeline.json 的 takeoff.diagram。
"""
from __future__ import annotations

import os
import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from .cad import GeometryIndex, load_dxf_document, resolve_dwg_dxf
from .cad_extractor import (
    RE_CIRCUIT_NO, RE_PHASE, RE_POWER, _BRANCH_CIRCUIT_NO, _is_spd_callout, _looks_like_cable,
    _native_claim, _row_device_field, _text_is_breaker, iter_panel_codes,
)
from .config import pipeline as _pipeline
from .native_takeoff import _Walker, _text_width
from .schema import Box, Circuit, Evidence, ExtraDevice, RawExtraction, Uncertainty
from .sheets import Sheet, detect_sheets, text_height_basis

_C = _pipeline()["takeoff"]["diagram"]
RE_CABINET = re.compile(_C["cabinet_type_regex"], re.I)
RE_IP = re.compile(_C["ip_regex"], re.I)
RE_QTY = re.compile(_C["quantity_regex"])
RE_CT = re.compile(_C["ct_ratio_regex"], re.I)
RE_SPD_DETAIL = re.compile(_C["spd_detail_regex"], re.I)
RE_SKIP = re.compile(_C["skip_text_regex"], re.I)
RE_RATING = re.compile(_C["breaker_rating_regex"], re.I)
RE_NOTE = re.compile(_C["note_regex"])
RE_CAPTION_REJECT = [re.compile(p, re.I) for p in _C["caption_code_reject_regexes"]]
CJK = re.compile(r"[一-鿿]")


@dataclass
class T:
    """一条图面文字（中心坐标、估算左右边界）。"""
    text: str
    x: float
    y: float
    h: float
    left: float
    right: float
    role: str = ""
    used: bool = False
    handle: str = ""
    owner: str = ""
    insert_path: str = ""


@dataclass
class Segment:
    """一台箱柜的出线回路（同一列里相邻的一段锚点）。"""
    rows: list[T]
    col_left: float
    incoming: list[T] = field(default_factory=list)
    headers: list[T] = field(default_factory=list)
    caption: T | None = None
    single: bool = False                 # 只有一个锚点、也不是谁的进线开关
    floor: float = float("-inf")         # 下面一台箱柜的抬头高度（进线区不越过它）

    @property
    def top(self) -> float:
        return max(r.y for r in self.rows)

    @property
    def bottom(self) -> float:
        return min(r.y for r in self.rows)


# ---------------------------------------------------------------- 文字

def _texts(records: list[Any]) -> list[T]:
    """去掉同位置重复文字（DWG 里常见两遍叠写），估算左右边界。"""
    seen: set = set()
    out: list[T] = []
    for r in records:
        text = (r.text or "").strip()
        if not text:
            continue
        h = float(r.height or 0) or 2.5
        key = (text, round(r.x / max(h * 0.3, 1e-6)), round(r.y / max(h * 0.3, 1e-6)))
        if key in seen:
            continue
        seen.add(key)
        w = _text_width(text, h)
        out.append(T(text=text, x=float(r.x), y=float(r.y), h=h, left=float(r.x) - w / 2, right=float(r.x) + w / 2,
                     handle=str(getattr(r, "handle", "") or ""), owner=str(getattr(r, "owner", "") or ""),
                     insert_path=str(getattr(r, "insert_path", "") or "")))
    return out


def _has(text: str, marks: list[str]) -> bool:
    return any(m in text for m in marks)


def _is_anchor(s: str) -> bool:
    """回路锚点 = 开关电器规格：词法上是开关，且写着额定电流（125A、D20、C16…）。
    浪涌保护器（SPD 4P）、“SPD厂家配套”这类不是回路开关。"""
    if not _text_is_breaker(s) or _is_spd_callout(s) or RE_SPD_DETAIL.search(s) or "配套" in s:
        return False
    if _has(s, _C["feed_marks"]) or "引至" in s:
        return False
    return bool(RE_RATING.search(s))


def _classify(t: T) -> str:
    s = t.text
    if RE_SKIP.search(s):
        return "calc"
    if RE_CIRCUIT_NO.match(s) or _BRANCH_CIRCUIT_NO.match(s):
        return "circuit_no"
    if _has(s, _C["ats_marks"]) and len(s) <= 30:
        return "ats"
    if _is_anchor(s):
        return "breaker"
    if _looks_like_cable(s):
        return "cable"
    if RE_CT.match(s.replace(" ", "")):
        return "ct"
    if RE_PHASE.match(s.replace(" ", "")):
        return "phase"
    if RE_POWER.match(s):
        return "power"
    if RE_CABINET.match(s):
        return "cabinet"
    if RE_IP.search(s) and len(s) <= 16:
        return "ip"
    if RE_QTY.search(s):
        return "qty"
    if _has(s, _C["install_marks"]) and len(s) <= 20:
        return "install"
    if _has(s, _C["spare_marks"]) and len(s) <= 8:
        return "spare"
    return "text"


# ---------------------------------------------------------------- 分列 / 分箱

def _columns(anchors: list[T], tol: float, max_gap: float, headers: list[T], h: float) -> list[list[T]]:
    """开关型号文字成列：左边缘接近、上下相邻（竖向间距不超过 max_gap）、中间没隔着箱柜抬头的连成一列。
    文字宽度是估算的，左边缘有误差；只按横坐标聚类会把挨得近的两台箱柜串成一列。"""
    parent = list(range(len(anchors)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(len(anchors)), key=lambda i: -anchors[i].y)
    # 典型行距：每个开关到正下方最近同列开关的距离，取中位数
    nearest = []
    for pos, i in enumerate(order):
        below = [anchors[i].y - anchors[j].y for j in order[pos + 1:]
                 if abs(anchors[i].left - anchors[j].left) <= tol and anchors[i].y - anchors[j].y <= max_gap]
        if below:
            nearest.append(min(below))
    pitch = statistics.median(nearest) if nearest else 3.3 * h
    for pos, i in enumerate(order):
        for j in order[pos + 1:]:
            if anchors[i].y - anchors[j].y > max_gap:
                break
            if abs(anchors[i].left - anchors[j].left) > tol:
                continue
            lo, hi = anchors[j].y, anchors[i].y
            # 抬头落在明显大于正常行距的空隙里才是两台箱柜的分界；和回路并排写在左侧的不算
            if hi - lo > _C["header_split_gap_factor"] * pitch and any(
                    lo < t.y < hi and abs(t.x - anchors[i].left) <= _C["header_left_heights"] * h for t in headers):
                continue
            parent[find(i)] = find(j)
    groups: dict[int, list[T]] = {}
    for i, a in enumerate(anchors):
        groups.setdefault(find(i), []).append(a)
    return [sorted(g, key=lambda t: -t.y) for g in sorted(groups.values(), key=lambda g: min(t.left for t in g))]


def _split_column(col: list[T], headers: list[T], h: float, sheet_pitch: float) -> list[list[T]]:
    """先在抬头处断开（一台箱柜一个抬头）；每一段再按它自己的行距突变断开（没有抬头的箱柜）。"""
    left = statistics.median(a.left for a in col)
    hs = [t for t in headers if left - _C["header_left_heights"] * h <= t.x <= left + _C["header_right_heights"] * h]
    parts: list[list[T]] = [[col[0]]]
    for prev, cur in zip(col, col[1:]):
        # 抬头在两台箱柜之间的空隙里才是分界；和回路并排写在左侧的抬头不切
        if (prev.y - cur.y) > _C["header_split_gap_factor"] * sheet_pitch and any(cur.y < t.y < prev.y for t in hs):
            parts.append([cur])
        else:
            parts[-1].append(cur)
    out: list[list[T]] = []
    for part in parts:
        gaps = [part[i].y - part[i + 1].y for i in range(len(part) - 1)]
        pitch = statistics.median(gaps) if gaps else 3 * h
        sub = [[part[0]]]
        for prev, cur in zip(part, part[1:]):
            if (prev.y - cur.y) > _C["box_gap_factor"] * pitch and not _uniform(gaps):
                sub.append([cur])
            else:
                sub[-1].append(cur)
        out += sub
    return out


def _uniform(gaps: list[float]) -> bool:
    """行距都差不多（变频柜主回路间夹控制线，行距大但均匀），就不按行距切。"""
    if len(gaps) < 2:
        return True
    return max(gaps) <= 1.35 * min(gaps)


# ---------------------------------------------------------------- 主流程

Unit = tuple[Box, list[Circuit], list[ExtraDevice]]


def _parse_sheet(ts: list[T], h: float, sheet_label: str, ev: _Evidence,
                 strict: bool = False, tables: list[tuple] = ()) -> tuple[list[Unit], list[str]]:
    """strict：平面图 / 图框外图面上，只认配到明确箱名的段（平面图上散落的开关型号不是箱柜）。"""
    for t in ts:
        t.role = _classify(t)
    # 开关柜表格（一列一柜）由 gtable 按网格解析，表格里的开关不在这里重复解析
    anchors = [t for t in ts if t.role == "breaker"
               and not any(b[0] <= t.x <= b[2] and b[1] <= t.y <= b[3] for b in tables)]
    if not anchors:
        return [], []
    headers = [t for t in ts if t.role in ("cabinet", "ip", "install")]

    columns = _columns(anchors, _C["column_tol_heights"] * h, _C["column_max_gap_heights"] * h, headers, h)
    # 全图典型行距：抬头只有落在明显大于它的空隙里，才是两台箱柜的分界
    gaps = [c[i].y - c[i + 1].y for c in columns for i in range(len(c) - 1)]
    sheet_pitch = statistics.median(gaps) if gaps else 3.3 * h
    segments: list[Segment] = []
    for col in columns:
        for part in _split_column(col, headers, h, sheet_pitch):
            segments.append(Segment(rows=part, col_left=statistics.median(a.left for a in part)))

    for s in segments:
        _extend_numbered_rows(s, ts, h, segments)
    # 单个锚点、左边挨着另一段且落在它的竖向范围里：是那台箱柜的进线开关
    multi = [s for s in segments if len(s.rows) >= 2]
    singles = [s for s in segments if len(s.rows) == 1]
    keep: list[Segment] = list(multi)
    for s in singles:
        a = s.rows[0]
        s.single = True
        host = None
        best = None
        for m in multi:
            if not (m.col_left - _C["incoming_left_heights"] * h <= a.x < m.col_left):
                continue
            if not (m.bottom - _C["incoming_below_heights"] * h <= a.y <= m.top + _C["incoming_above_heights"] * h):
                continue
            d = m.col_left - a.x
            if best is None or d < best:
                host, best = m, d
        if host is None:
            # 表格式系统图：总开关写在出线列顶上一行，同一行左边写着电源来源（“由…引来”）
            for m in multi:
                if 0 < a.y - m.top <= _C["incoming_above_heights"] * h and \
                        m.col_left - h <= a.left <= m.col_left + _C["header_right_heights"] * h and \
                        any(_is_feed(t) and abs(t.y - a.y) <= max(h, a.h) and t.x < a.x for t in ts):
                    host = m
                    break
        if host is not None:
            host.incoming.append(a)
        else:
            keep.append(s)
    segments = keep

    # 抬头归属：在段顶上方、列左右范围内
    for s in segments:
        band = [t for t in headers
                if s.top - _C["header_below_top_heights"] * h < t.y <= s.top + _C["header_above_heights"] * h
                and s.col_left - _C["header_left_heights"] * h <= t.x <= s.col_left + _C["header_right_heights"] * h]
        # 安装方式与柜型、防护等级写在同一条抬头线上；没有柜型时，只认紧挨着首行的
        line_ys = [t.y for t in band if t.role in ("cabinet", "ip")]
        s.headers = [t for t in band if t.role != "install"
                     or (line_ys and min(abs(t.y - y) for y in line_ys) <= 2 * h)
                     or (not line_ys and t.y <= s.top + _C["install_above_heights"] * h)]

    for s in segments:
        below = [o for o in segments if o is not s and o.top < s.bottom and abs(o.col_left - s.col_left) <= 5 * h]
        if below:
            nxt = max(below, key=lambda o: o.top)
            s.floor = max([nxt.top] + [t.y for t in nxt.headers]) + h
    row_texts = _row_text_ids(segments, ts, h)
    _assign_captions(segments, ts, h, row_texts)

    segments = _merge_uncaptioned(segments, headers, h)
    units: list[Unit] = []
    warnings: list[str] = []
    unnamed = 0
    for s in sorted(segments, key=lambda s: (-s.top, s.col_left)):
        caption_install = ""
        strong = s.caption is not None and _has(s.caption.text, _C["caption_name_marks"])
        if (s.single or strict) and not strong:
            continue                     # 图例、说明、平面图上的开关型号，不是一台箱柜
        if s.caption is not None:
            codes = iter_panel_codes(s.caption.text)
            code = codes[0] if codes else ""
            name = re.sub(r"\s+", "", re.sub(re.escape(code), "", s.caption.text, count=1, flags=re.I))
            tail = re.search(r"共\d+台.*$", name)
            if tail:
                inst = re.search(r"[（(]([^)）]*)[)）]", tail.group(0))
                if inst and _has(inst.group(1), _C["install_marks"]):
                    caption_install = inst.group(1)
                name = name[:tail.start()]
            name = name.strip("：:，,()（）")
            if name.endswith("系统图") and not re.search(r"[箱柜屏盘]", name):
                name = name[:-3] + "配电箱"      # “道路照明系统图”说的是道路照明配电箱
            name = re.sub(r"系统图$", "", name)
        else:
            unnamed += 1
            code, name = f"未识别箱号{unnamed}", ""
            warnings.append(f"{sheet_label}：有一台箱柜（{len(s.rows)} 条回路，首行 {s.rows[0].text}）没找到箱号，请在图上核对")
        box = Box(code=code, name=name, note=sheet_label)
        _fill_header(box, s, ts, h, ev)
        notes = _box_notes(s, ts, h)
        if notes:
            box.note = "；".join([box.note] + notes)
        if not box.install and caption_install:
            box.install = caption_install
        circuits = _row_circuits(s, ts, h, code, _reach(s, h, segments), ev)
        circuits = _incoming_circuits(s, ts, h, code, ev) + circuits
        units.append((box, circuits, _incoming_devices(s, ts, h, code)))
    return units, warnings


def _extend_numbered_rows(s: Segment, ts: list[T], h: float, segments: list[Segment]) -> None:
    """回路号那一列上下延伸出去、却没有开关的编号行（E2、E3 只写了编号和“消防备用”），也是本箱的回路。"""
    def nearest_column(t: T) -> bool:
        # 编号归左侧最近的那一列开关：中间若隔着别的开关列，就不是本列的回路号
        return not any(o is not s and s.col_left < o.col_left < t.x
                       and o.bottom - 6 * h <= t.y <= o.top + 6 * h for o in segments)

    def beside(t: T, r: T) -> bool:
        hh = max(h, r.h)
        return abs(t.y - r.y) <= hh and (r.x < t.x <= r.right + 15 * hh or r.left - 12 * hh <= t.x < r.left)

    nos = [t for t in ts if t.role == "circuit_no" and not t.used and RE_CIRCUIT_NO.match(t.text)
           and any(beside(t, r) for r in s.rows) and nearest_column(t)]
    if not nos:
        return
    no_x = statistics.median(t.x for t in nos)
    h = max(h, statistics.median(t.h for t in nos))      # 这一块的字可能比全图字高大
    prefixes = {re.match(r"[A-Za-z]*", t.text).group(0).upper() for t in nos}
    rows = sorted(s.rows, key=lambda t: -t.y)
    gaps = [rows[i].y - rows[i + 1].y for i in range(len(rows) - 1)]
    pitch = statistics.median(gaps) if gaps else 3.5 * h
    on_rows = {id(t) for t in nos}
    anchor_rows = [r for o in segments for r in o.rows if r.role == "breaker"]
    column = sorted((t for t in ts if t.role == "circuit_no" and abs(t.x - no_x) <= h and id(t) not in on_rows
                     and not any(beside(t, r) for r in anchor_rows)
                     and re.match(r"[A-Za-z]*", t.text).group(0).upper() in prefixes
                     and not any(abs(t.y - r.y) <= 0.8 * h for r in s.rows)), key=lambda t: -t.y)
    added = True
    while added:
        added = False
        top, bottom = max(r.y for r in s.rows), min(r.y for r in s.rows)
        for t in column:
            if t in s.rows or t.used:
                continue
            if 0 < t.y - top <= 1.6 * pitch or 0 < bottom - t.y <= 1.6 * pitch:
                s.rows.append(t)
                t.used = True            # 一条编号行只归一台箱柜
                added = True


def _merge_uncaptioned(segments: list[Segment], headers: list[T], h: float) -> list[Segment]:
    """按行距切开却没配到箱名的一段：同一列里紧挨着一台有箱名的箱柜、中间没有抬头隔开，就是它的一部分。"""
    keep = list(segments)
    for s in sorted(segments, key=lambda s: -len(s.rows)):
        if s.caption is not None or s.single or s not in keep:
            continue
        best = None
        for o in keep:
            if o is s or o.caption is None or abs(o.col_left - s.col_left) > _C["column_tol_heights"] * h:
                continue
            lo, hi = (s.top, o.bottom) if s.top < o.bottom else (o.top, s.bottom)
            gap = max(hi - lo, 0.0)
            if gap > _C["merge_gap_heights"] * h:
                continue
            if any(lo < t.y < hi and abs(t.x - s.col_left) <= _C["header_left_heights"] * h for t in headers):
                continue
            if best is None or gap < best[0]:
                best = (gap, o)
        if best is not None:
            o = best[1]
            o.rows += s.rows
            o.incoming += s.incoming
            keep.remove(s)
    return keep


def _row_bands(s: Segment, h: float) -> list[tuple[T, float, float]]:
    rows = sorted(s.rows, key=lambda t: -t.y)
    gaps = [rows[i].y - rows[i + 1].y for i in range(len(rows) - 1)]
    pitch = statistics.median(gaps) if gaps else 3.3 * h
    bands = []
    for i, r in enumerate(rows):
        up = (rows[i - 1].y + r.y) / 2 if i > 0 else r.y + pitch / 2
        down = (r.y + rows[i + 1].y) / 2 if i + 1 < len(rows) else r.y - pitch / 2
        bands.append((r, down, up))
    return bands


def _reach(s: Segment, h: float, segments_right: list[Segment]) -> float:
    """出线行向右最远到哪：不越过右边相邻箱柜的进线区（它的进线开关左边还有电源来源、电缆）。"""
    right = s.col_left + _C["row_reach_heights"] * h
    for o in segments_right:
        if o.col_left > s.col_left + 3 * h and not (o.bottom > s.top or o.top < s.bottom):
            edge = min([o.col_left] + [a.left for a in o.incoming])
            right = min(right, edge - _C["neighbor_margin_heights"] * h)
    return right


def _row_text_ids(segments: list[Segment], ts: list[T], h: float) -> set[int]:
    """落在某条回路行范围里的文字（负荷名称里也会出现箱号，不能当箱名）。"""
    ids: set[int] = set()
    for s in segments:
        reach = _reach(s, h, segments)
        for r, down, up in _row_bands(s, h):
            for t in ts:
                if max(down, r.y - 1.5 * h) <= t.y <= min(up, r.y + 1.5 * h) and r.left - 0.5 * h <= t.x <= reach:
                    ids.add(id(t))
    return ids


def _assign_captions(segments: list[Segment], ts: list[T], h: float, row_ids: set[int]) -> None:
    def candidate(t: T) -> bool:
        if id(t) in row_ids or len(t.text) > 30 or not iter_panel_codes(t.text):
            return False
        if any(rx.match(iter_panel_codes(t.text)[0]) for rx in RE_CAPTION_REJECT):
            return False
        if _has(t.text, _C["feed_marks"]) or re.search(r"[:：]", t.text):
            return False
        if not CJK.search(t.text) and any(_is_feed(f) and abs(f.y - t.y) <= 0.6 * h and abs(f.x - t.x) <= 15 * h
                                           for f in feeds):
            return False                 # “ET1C08P03 由…引来”里的电源来源编号
        return t.role in ("text", "cabinet", "qty")

    feeds = [t for t in ts if _is_feed(t)]

    cands = [t for t in ts if candidate(t)]
    strong = [t for t in cands if _has(t.text, _C["caption_name_marks"])]
    weak = [t for t in cands if t not in strong]

    def dist(s: Segment, t: T, need_top: bool) -> float | None:
        if not (s.col_left - _C["caption_left_heights"] * h <= t.x <= s.col_left + _C["caption_right_heights"] * h):
            return None
        if need_top and t.y < s.top - h:
            return None
        if t.y > s.top:
            gap = t.y - s.top
        elif t.y < s.bottom:
            gap = s.bottom - t.y
        else:
            gap = 0.0
        if gap > _C["caption_max_gap_heights"] * h:
            return None
        return gap + abs(t.x - s.col_left) * 0.05

    for pool, need_top in ((strong, False), (weak, True)):
        pairs = []
        for s in segments:
            if s.caption is not None:
                continue
            for t in pool:
                d = dist(s, t, need_top)
                if d is not None:
                    pairs.append((d, id(s), id(t), s, t))
        taken_s, taken_t = set(), set()
        for d, sid, tid, s, t in sorted(pairs, key=lambda p: p[0]):
            if sid in taken_s or tid in taken_t:
                continue
            s.caption = t
            t.used = True
            taken_s.add(sid)
            taken_t.add(tid)


class _Evidence:
    """把字段指回写出它的那条 CAD 文字（handle + 所在块 + 位置），供核对与“依据”展示。"""

    def __init__(self, file_sha256: str):
        self.sha = file_sha256
        self.store: dict[str, Evidence] = {}

    def claim(self, t: T | None, value: str = ""):
        if t is None:
            return None
        source = {"handle": t.handle, "owner": t.owner or "modelspace",
                  "insert_path": t.insert_path,
                  "layout": "Model", "file_sha256": self.sha}
        return _native_claim(self.store, value or t.text, source, "")

    def bind(self, target: Any, prefix: str, fields: dict[str, T | None], values: dict[str, str] | None = None) -> None:
        claims = {}
        for name, t in fields.items():
            g = self.claim(t, (values or {}).get(name, ""))
            if g is not None:
                claims[f"{prefix}.{name}"] = g
        target.claims = claims


def _fill_header(box: Box, s: Segment, ts: list[T], h: float, ev: _Evidence) -> None:
    near = list(s.headers)
    if s.caption is not None:
        near += [t for t in ts if t.role in ("cabinet", "ip", "install", "qty")
                 and abs(t.y - s.caption.y) <= 3 * h and abs(t.x - s.caption.x) <= 25 * h]
    # 箱尾标注（“共1台 落地安装”）写在本箱下方、下一台箱柜上方
    # 箱名写在箱下方时，箱尾标注只会在箱名之下
    floor = s.floor
    if s.caption is not None and s.caption.y < s.bottom:
        floor = max(floor, s.caption.y - 3 * h)
    near += [t for t in ts if t.role in ("install", "qty") and floor < t.y < s.bottom
             and s.col_left - 30 * h <= t.x <= s.col_left + 40 * h and t not in near]
    used: dict[str, T | None] = {"code": s.caption}
    # 图上写了“共N台”才算台数确认；没写的按 1 台候选，项目总量不放行
    box.quantity_confirmed = False
    for t in sorted(near, key=lambda t: -t.y):
        if t.role == "cabinet" and not box.size:
            box.size = RE_CABINET.match(t.text).group(0)
            used["size"] = t
        elif t.role == "ip" and not box.ip_rating:
            box.ip_rating = RE_IP.search(t.text).group(0).replace(" ", "")
            used["ip_rating"] = t
        elif t.role == "install" and not box.install:
            box.install = t.text.strip()
            used["install"] = t
        m = RE_QTY.search(t.text)
        if m:
            box.quantity = int(m.group(1))
            box.quantity_confirmed = True
            used["quantity"] = t
    if s.caption is not None:
        m = RE_QTY.search(s.caption.text)
        if m:
            box.quantity = int(m.group(1))
            box.quantity_confirmed = True
    ev.bind(box, "box", used)


def _described_cable(items: list[T], ref: T, h: float) -> T | None:
    """“电缆型号规格详见弱电专业”“电缆型号详见上级配电”这类文字写在电缆栏里，也是这条回路的电缆。"""
    cands = [t for t in items if t.role == "text" and "电缆" in t.text and ("详见" in t.text or "规格" in t.text)
             and abs(t.y - ref.y) <= 1.5 * h]
    return min(cands, key=lambda t: abs(t.y - ref.y), default=None)


def _box_notes(s: Segment, ts: list[T], h: float) -> list[str]:
    """箱柜附近的备注（“备注：…”“注：…”，或提到铜排、柜内线等柜内材料的文字），记进箱柜备注。"""
    lo = min(s.bottom, s.caption.y if s.caption else s.bottom) - 6 * h
    hi = max(s.top, s.caption.y if s.caption else s.top) + 6 * h
    out = []
    for t in ts:
        if t.used or not (lo <= t.y <= hi) or not (s.col_left - 40 * h <= t.x <= s.col_left + 60 * h):
            continue
        if RE_NOTE.match(t.text) or _has(t.text, _C["material_marks"]):
            out.append(t.text)
            t.used = True
    return out


def _load_lines(first: T, pool: list[T], h: float) -> list[T]:
    """负荷名称写成两行（“380V 32A / 三相接线柱”）：同一竖列、紧挨着的上下文字拼起来。"""
    lines = [first]
    for direction in (-1, 1):
        cur = first
        while True:
            nxt = [t for t in pool if t not in lines and min(t.right, cur.right) - max(t.left, cur.left) > 0
                   and 0 < direction * (cur.y - t.y) <= 1.7 * max(cur.h, t.h)]
            if not nxt:
                break
            cur = min(nxt, key=lambda t: abs(t.y - cur.y))
            lines.append(cur)
    return sorted(lines, key=lambda t: -t.y)


def _row_circuits(s: Segment, ts: list[T], h: float, code: str, reach: float, ev: _Evidence) -> list[Circuit]:
    out = []
    bands = _row_bands(s, h)
    for r, down, up in bands:
        r.used = True
    # 回路号所在的那一列：“备用”写在这一列才是回路号，写在负荷栏就是负荷名称
    no_xs = [t.x for r, down, up in bands for t in ts
             if t.role == "circuit_no" and abs(t.y - r.y) <= 0.8 * h and r.x < t.x <= reach]
    no_col = statistics.median(no_xs) if no_xs else None
    left_cols = {role: _left_column(bands, ts, h, role) for role in ("circuit_no", "phase")}
    load_x = _load_column(bands, ts, h, reach)
    for r, down, up in bands:
        band = [t for t in ts if t is not r and down <= t.y <= up and r.left - 0.5 * h <= t.x <= reach
                and not t.used and t.role != "breaker"]
        marker = r.role != "breaker"          # 没有开关、只有编号的行
        c = Circuit(box=code, breaker="" if marker else r.text)

        def pick(role: str) -> T | None:
            items = [t for t in band if t.role == role]
            return min(items, key=lambda t: (abs(t.y - r.y), t.x)) if items else None

        def pick_left(role: str) -> T | None:
            # 表格式系统图：回路号、相序成列写在开关左边
            col = left_cols[role]
            if col is None:
                return None
            items = [t for t in ts if not t.used and t.role == role and abs(t.x - col) <= 3 * h
                     and abs(t.y - r.y) <= max(h, r.h)]
            return min(items, key=lambda t: abs(t.y - r.y), default=None)

        on_line = [t for t in band if t.role == "circuit_no" and abs(t.y - r.y) <= 0.8 * h]
        spare = pick("spare")
        if spare is not None and no_col is not None and abs(spare.x - no_col) > 3 * h:
            spare = None
        if spare is not None and spare.text.strip() not in _C["spare_marks"]:
            spare = None                  # “备用照明”是负荷名称，不是回路号栏里的“备用”
        if not marker and not on_line:
            # 回路号写在开关左边的版式
            left = [t for t in ts if not t.used and t.role == "circuit_no" and abs(t.y - r.y) <= max(h, r.h)
                    and r.left - 12 * max(h, r.h) <= t.x < r.left]
            if left:
                on_line = [max(left, key=lambda t: t.x)]
        if not marker and not on_line:
            left_no = pick_left("circuit_no")
            on_line = [left_no] if left_no is not None else []
        no = r if marker else (min(on_line, key=lambda t: t.x) if on_line else (pick("circuit_no") or spare))
        ph = pick("phase") or pick_left("phase")
        cable = pick("cable") or _described_cable(band, r, h)
        ct = min((t for t in band if t.role == "ct" and t.y >= r.y - 0.3 * h), key=lambda t: abs(t.y - r.y),
                 default=None)
        devs: dict[str, T] = {}
        for t in band:
            fld = _row_device_field(t.text)
            if fld in ("contactor", "thermal") and fld not in devs:
                devs[fld] = t
        x_floor = max([t.x for t in (cable, no, ph) if t is not None] or [r.right])
        pool = [t for t in band if t.role in ("text", "spare", "circuit_no") and t is not no and t not in devs.values()
                and t.x > x_floor - 0.5 * h and not _is_feed(t)
                and (t.role == "circuit_no" or CJK.search(t.text) or iter_panel_codes(t.text)
                     or re.search(r"\d+\s*[VA]", t.text))]
        # 负荷名称写在本行线上或紧挨着；高度差不多时取横向离本行近的（远处可能是别的箱的字）
        if load_x is not None:
            pool = [t for t in pool if abs(t.left - load_x) <= _C["load_column_tol_heights"] * h]
        load = min(pool, key=lambda t: abs(t.y - r.y) + 0.05 * abs(t.x - x_floor), default=None)
        if load is not None and abs(load.y - r.y) > 2.0 * h:
            load = None
        wrap = [t for t in ts if not t.used and t.role in ("text", "spare") and t is not r
                and abs(t.y - r.y) <= 2.5 * h and t not in devs.values() and t is not no]
        lines = _load_lines(load, pool + [t for t in wrap if t not in pool], h) if load is not None else []
        # 功率写在负荷名称下方，可能略低于本行范围：在负荷正下方 2 个字高内找
        ref = lines[-1] if lines else r
        power = [t for t in band if t.role == "power"] + [
            t for t in ts if t.role == "power" and not t.used and lines
            and ref.y - 2.2 * h <= t.y < down and min(abs(t.x - ln.x) for ln in lines) <= 4 * h]
        pw = min(power, key=lambda t: abs(t.x - ref.x) + abs(t.y - ref.y)) if power else None
        c.circuit_no = no.text if no else ""
        c.phase = ph.text if ph else ""
        c.cable = cable.text if cable else ""
        c.ct = ct.text if ct else ""
        c.contactor = devs["contactor"].text if "contactor" in devs else ""
        c.thermal = devs["thermal"].text if "thermal" in devs else ""
        c.load_name = "\n".join(t.text for t in lines)
        c.power_kw = pw.text if pw else ""
        notes = [t.text for t in pool if t not in lines and _has(t.text, _C["spare_marks"])]
        if notes:
            c.note = "；".join(notes)
        for t in (no, ph, cable, ct, pw, *lines, *devs.values()):
            if t is not None:
                t.used = True
        ev.bind(c, "circuit", {"breaker": None if marker else r, "circuit_no": no, "phase": ph, "cable": cable, "ct": ct,
                               "load_name": load, "power_kw": pw,
                               "contactor": devs.get("contactor"), "thermal": devs.get("thermal")},
                {"load_name": c.load_name})
        out.append(c)
    out += _branch_lines(s, ts, h, code, reach, ev)
    return out


def _left_column(bands: list[tuple[T, float, float]], ts: list[T], h: float, role: str) -> float | None:
    """开关左边成列的回路号 / 相序（表格式系统图）：多数行在同一竖列上都有，才认这一列。
    零散的一两个不算，免得把进线区的文字认成出线回路的字段。"""
    xs = []
    for r, down, up in bands:
        if r.role != "breaker":
            continue
        cands = [t for t in ts if not t.used and t.role == role and abs(t.y - r.y) <= max(h, r.h)
                 and r.left - _C["left_column_heights"] * h <= t.x < r.left]
        if cands:
            xs.append(max(cands, key=lambda t: t.x).x)
    if len(xs) < max(2, 0.6 * len(bands)):
        return None
    col = statistics.median(xs)
    if sum(abs(x - col) <= 3 * h for x in xs) < max(2, 0.6 * len(bands)):
        return None
    return col


def _load_column(bands: list[tuple[T, float, float]], ts: list[T], h: float, reach: float) -> float | None:
    """负荷名称栏的左边缘：多数行的负荷名称左对齐。行少于 3 条时不估。"""
    lefts = []
    for r, down, up in bands:
        on_line = [t for t in ts if not t.used and abs(t.y - r.y) <= 0.8 * h and r.x < t.x <= reach
                   and t.role == "text" and CJK.search(t.text)]
        if on_line:
            lefts.append(min(on_line, key=lambda t: t.x).left)
    if len(lefts) < 3:
        return None
    return statistics.median(lefts)


def _branch_lines(s: Segment, ts: list[T], h: float, code: str, reach: float, ev: _Evidence) -> list[Circuit]:
    """没有开关、只有编号和电缆的支线（变频柜的 D01-C1 控制线）也是图上的一条回路。"""
    out = []
    rows = sorted(r.y for r in s.rows)
    pitch = statistics.median([b - a for a, b in zip(rows, rows[1:])]) if len(rows) > 1 else 3 * h
    for t in ts:
        if t.used or t.role != "circuit_no" or not (s.bottom - max(2 * h, 0.9 * pitch) <= t.y <= s.top + h):
            continue
        if not (s.col_left - 2 * h <= t.x <= reach):
            continue
        # 支线编号可能写在线下，电缆和去向写在线上（变频柜控制线）：往上多看两个字高
        row = [o for o in ts if not o.used and o is not t and t.y - 1.5 * h <= o.y <= t.y + 2.0 * h
               and t.left - h <= o.x <= reach]
        cable = min((o for o in row if o.role == "cable"), key=lambda o: (abs(o.y - t.y), abs(o.x - t.x)),
                    default=None) or _described_cable(row, t, h)
        ref_y = cable.y if cable is not None else t.y
        load = min((o for o in row if o.role == "text" and o is not cable and CJK.search(o.text)
                    and (cable is None or o.x > cable.x) and abs(o.y - ref_y) <= 1.5 * h),
                   key=lambda o: (abs(o.y - ref_y), abs(o.x - t.x)), default=None)
        if cable is None and load is None:
            continue
        c = Circuit(box=code, circuit_no=t.text, cable=cable.text if cable else "",
                    load_name=load.text if load else "")
        for o in (t, cable, load):
            if o is not None:
                o.used = True
        ev.bind(c, "circuit", {"circuit_no": t, "cable": cable, "load_name": load})
        out.append(c)
    return out


def _incoming_zone(s: Segment, ts: list[T], h: float) -> list[T]:
    """进线区：出线列左侧、本箱竖向范围内；出线列正下方（进线母线相序常写在那里）也算。
    向下最多 incoming_below_heights 个字高，且不越过下面一台箱柜的抬头。"""
    lo = max(s.bottom - _C["incoming_below_heights"] * h, s.floor)
    hi = s.top + _C["incoming_above_heights"] * h
    x0 = s.col_left - _C["incoming_left_heights"] * h
    x_under = s.col_left + _C["incoming_under_heights"] * h
    return [t for t in ts if lo <= t.y <= hi and not t.used
            and (x0 <= t.x < s.col_left or (t.y < s.bottom and s.col_left - 2 * h <= t.x <= x_under))]


def _is_feed(t: T) -> bool:
    """电源来源：“由…引来”，或“箱号:回路号”（02AP02:N3、市电故障检测:02AL01:N10）。"""
    if _has(t.text, _C["feed_marks"]):
        return True
    parts = re.split(r"[:：]", t.text)
    for head, tail in zip(parts, parts[1:]):
        code = head.strip()[-12:]
        if iter_panel_codes(code) and re.match(r"\s*[A-Za-z]*\d+", tail):
            return True
    return False


def _feed_groups(zone: list[T], h: float) -> list[list[T]]:
    """电源来源说明按行分组：同一行的“02AP02:N3 由02AP02引来”是一路进线。"""
    feeds = sorted((t for t in zone if _is_feed(t)), key=lambda t: -t.y)
    groups: list[list[T]] = []
    for f in feeds:
        if groups and abs(groups[-1][0].y - f.y) <= 0.6 * h:
            groups[-1].append(f)
        else:
            groups.append([f])
    for g in groups:
        # 电源来源编号（ET1C08P04）写在“由…引来”左边同一行
        g += [t for t in zone if t not in g and t.role == "text" and iter_panel_codes(t.text)
              and not CJK.search(t.text) and abs(t.y - g[0].y) <= 0.6 * h and t.x < min(o.x for o in g)]
        g.sort(key=lambda t: t.x)
    return groups


def _incoming_circuits(s: Segment, ts: list[T], h: float, code: str, ev: _Evidence) -> list[Circuit]:
    """进线：每路电源来源说明是一路进线，配上离它最近的进线开关（若有）与电缆。
    “市电故障检测”这类检测、信号线不是供电进线，回路号留空。"""
    out = []
    zone = _incoming_zone(s, ts, h)
    groups = _feed_groups(zone, h)
    breakers = sorted(s.incoming, key=lambda a: -a.y)
    pairs: list[tuple[T | None, list[T]]] = []
    for a in breakers:
        g = min(groups, key=lambda g: abs(g[0].y - a.y), default=None)
        if g is not None:
            groups.remove(g)
        pairs.append((a, g or []))
    pairs += [(None, g) for g in groups]
    for a, g in pairs:
        ref_y = g[0].y if g else a.y
        signal = bool(g) and _has(" ".join(t.text for t in g), _C["signal_feed_marks"])
        c = Circuit(box=code, breaker=a.text if a else "",
                    circuit_no="" if signal else "进线", load_name="" if signal else "进线")
        # 电缆写在进线开关与来源说明之间（或来源说明旁），取离来源说明最近的一条
        lo = min(ref_y, a.y if a else ref_y) - 2.5 * h
        hi = max(ref_y, a.y if a else ref_y) + 2.5 * h
        cable = min((t for t in zone if t.role == "cable" and not t.used and lo <= t.y <= hi),
                    key=lambda t: abs(t.y - ref_y), default=None)
        if cable is None:
            cable = _described_cable([t for t in zone if not t.used and t not in g], g[0] if g else a, h)
        phase = None
        if not signal and len(pairs) == 1:      # 双电源两路进线时，母线相序不归某一路
            phase = min((t for t in zone if t.role == "phase" and t.text.upper().startswith("L1") and not t.used),
                        key=lambda t: abs(t.y - (a.y if a else ref_y)), default=None)
        c.cable = cable.text if cable else ""
        c.phase = phase.text if phase else ""
        c.note = " ".join(t.text for t in g)
        for t in (a, cable, phase, *g):
            if t is not None:
                t.used = True
        ev.bind(c, "circuit", {"breaker": a, "cable": cable, "phase": phase,
                               "note": g[0] if g else None}, {"note": c.note})
        out.append(c)
    return out


def _incoming_devices(s: Segment, ts: list[T], h: float, code: str) -> list[ExtraDevice]:
    zone = [t for t in _incoming_zone(s, ts, h) if t.role != "calc"]
    devices: list[ExtraDevice] = []
    for a in [t for t in zone if t.role == "ats"]:
        grade = next((t for t in zone if re.fullmatch(r"(?:PC|CB)级", t.text) and abs(t.y - a.y) <= 2.5 * h), None)
        devices.append(ExtraDevice(name=a.text, spec=grade.text if grade else "", unit="台", quantity=1,
                                   used_in=code))
        a.used = True
        if grade is not None:
            grade.used = True
    meters = [t for t in zone if _has(t.text, _C["meter_marks"])]
    for m in meters:
        devices.append(ExtraDevice(name="多功能仪表" if "多功能" in m.text else m.text, spec=m.text,
                                   unit="只", quantity=1, used_in=code))
        m.used = True
    for b in [t for t in zone if _has(t.text, _C["spd_backup_marks"])]:
        partner = next((t for t in zone if "配套" in t.text and abs(t.y - b.y) <= 2 * h), None)
        devices.append(ExtraDevice(name="SPD后备保护器", spec=partner.text if partner else b.text,
                                   unit="只", quantity=1, used_in=code))
        b.used = True
        if partner is not None:
            partner.used = True
    spd_lines = [t for t in zone if (_is_spd_callout(t.text) or RE_SPD_DETAIL.search(t.text))
                 and not _has(t.text, _C["spd_backup_marks"]) and "配套" not in t.text]
    if spd_lines:
        spd_lines.sort(key=lambda t: (-t.y, t.x))
        devices.append(ExtraDevice(name="浪涌保护器", spec=" ".join(t.text.rstrip(":：") for t in spd_lines),
                                   unit="只", quantity=1, used_in=code))
        for t in spd_lines:
            t.used = True
    if s.incoming:
        a = s.incoming[0]
        for t in [t for t in zone if t.role == "ct" and abs(t.y - a.y) <= 2 * h]:
            three = bool(meters)
            devices.append(ExtraDevice(name="电流互感器", spec=t.text, unit="只",
                                       quantity=int(_C["incoming_ct_count"]) if three else 1, used_in=code,
                                       note="进线计量互感器，按三相计量计 3 只，请核对" if three else ""))
            t.used = True
    return devices


# ---------------------------------------------------------------- 入口

def _system_sheets(doc: Any, index: GeometryIndex, records: list[Any]) -> list[tuple[str, list[Any], bool]]:
    """要解析的图面：除设计说明 / 目录 / 材料表外的全部图幅（系统图也常画在平面图上），
    再加上不在任何图框里的文字（有些图框块只包住图签条）。没有成列开关的图面解析不出东西。"""
    sheets: list[Sheet] = detect_sheets(doc, index, records)
    out = []
    framed: set[int] = set()
    for s in sheets:
        framed.update(id(t) for t in s.texts)
        if s.kind == "notes":
            continue
        out.append((" ".join(v for v in (s.number, s.name) if v) or "图幅", s.texts, s.kind == "system"))
    loose = [t for t in records if id(t) not in framed]
    if loose:
        out.append(("图框外图面" if sheets else "整图", loose, not sheets))
    return out


def _file_sha(src: Any) -> str:
    if not isinstance(src, str) or not os.path.isfile(src):
        return ""
    from .cad_ledger import sha256_file
    try:
        return sha256_file(src)
    except OSError:
        return ""


def extract_takeoff(dxf_or_doc: Any) -> RawExtraction:
    """读出全部系统图上的箱柜、回路与附加器件。"""
    if isinstance(dxf_or_doc, str):
        path = resolve_dwg_dxf(dxf_or_doc) if dxf_or_doc.lower().endswith(".dwg") else dxf_or_doc
        doc = load_dxf_document(path)
    else:
        doc = dxf_or_doc
    records = _Walker(doc).layout_texts(doc.modelspace(), "模型空间")
    ev = _Evidence(_file_sha(dxf_or_doc))
    index = GeometryIndex(doc)
    h_basis = text_height_basis(records)

    tb, tc, td, table_boxes = _switchgear_tables(doc)
    units: list[Unit] = []
    warnings: list[str] = []
    for label, recs, is_system in _system_sheets(doc, index, records):
        ts = _texts(recs)
        h = statistics.median([t.h for t in ts]) if ts else h_basis
        u, w = _parse_sheet(ts, h, label, ev, strict=not is_system, tables=table_boxes)
        units += u
        warnings += w
    units = _merge_same_code(units)

    boxes = [u[0] for u in units]
    circuits = [c for u in units for c in u[1]]
    devices = [d for u in units for d in u[2]]
    known = {b.code for b in boxes}
    fresh = {b.code for b in tb if b.code not in known}
    boxes += [b for b in tb if b.code in fresh]
    circuits += [c for c in tc if c.box in fresh]
    devices += [d for d in td if (d.used_in or "").split(" ")[0] in fresh]
    return RawExtraction(
        boxes=boxes, circuits=circuits, extra_devices=devices, requirements=[], evidence_store=ev.store,
        uncertainties=[Uncertainty(text=w, location="原生读图", detail=w, source="program") for w in warnings],
    )


def _switchgear_tables(doc: Any) -> tuple[list[Box], list[Circuit], list[ExtraDevice], list[tuple]]:
    """GGD 一类开关柜系统图是表格（一列一柜），按网格解析；没有出线的柜（进线柜、应急启动柜）也保留。
    返回 (箱柜, 回路, 器件, 解析出柜子的表格范围)。"""
    from .gtable import parse_switchgear_table, reconstruct_gtable_from_dxf

    boxes: list[Box] = []
    circuits: list[Circuit] = []
    devices: list[ExtraDevice] = []
    extents: list[tuple] = []
    try:
        tables = reconstruct_gtable_from_dxf(doc)
    except Exception:
        return boxes, circuits, devices, extents
    seen: set[str] = set()
    for tbl in tables:
        try:
            tb, tc, td = parse_switchgear_table(tbl)
        except Exception:
            continue
        codes = {b.code for b in tb if b.code and b.code not in seen}
        if not codes:
            continue
        extents.append(tuple(tbl.bbox))
        for b in tb:
            if b.code in codes:
                boxes.append(b)
                seen.add(b.code)
        circuits += [c for c in tc if c.box in codes]
        devices += [d for d in td if (d.used_in or "").split(" ")[0] in codes]
    return boxes, circuits, devices, extents


def _merge_same_code(units: list[Unit]) -> list[Unit]:
    """同一箱号在不同图上各画一次（干线图里的示意 + 系统图）：整台保留回路多的那份，不叠加。"""
    best: dict[str, Unit] = {}
    order: list[str] = []
    for u in units:
        code = u[0].code
        if code not in best:
            order.append(code)
            best[code] = u
        elif len(u[1]) > len(best[code][1]):
            best[code] = u
    return [best[c] for c in order]
