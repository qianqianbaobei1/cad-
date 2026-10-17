# -*- coding: utf-8 -*-
"""图幅识别：把一张 DWG/DXF 模型空间拆成一张张真实图纸。

思路同 CAD快速看图：不按文字位置猜裁剪框，而是认图框本身。
1. 候选外框来自三处：模型空间里的闭合矩形、图框块定义里的闭合矩形（设计院的动态图框块
   把 A0~A3 加长幅面全部叠在一个块里，可见性参数转换后读不到）、块参照包围盒。
2. 同一个图框块的多个幅面变体共用右下角插入点。真实幅面的边框不会切过图面内容，
   取「边框不切内容」的变体里面积最小的一个。
3. 图签里的图号若在图纸目录里出现，目录同一行的文字就是图名；否则取图签里的图名文字。
长度阈值全部按全图文字字高换算，不写死图纸单位（1:1 与 1:100 绘制都适用）。
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from .config import domain as _domain, pipeline as _pipeline

_CFG = _pipeline()["takeoff"]["sheets"]
_DOM = _domain()["cad"]
SYSTEM_MARKERS = tuple(_DOM["system_title_markers"])
PLAN_MARKERS = tuple(_DOM["plan_title_markers"])
NOTE_MARKERS = tuple(_CFG["note_title_markers"])

SHEET_NO_RE = re.compile(r"^(?=.*\d)[\u4e00-\u9fffA-Z0-9]{1,}(?:[-_][A-Z0-9]+){1,6}$")
DATE_RE = re.compile(r"^\d{2,4}[-_./]\d{1,2}(?:[-_./]\d{1,2})?$")
CJK_RE = re.compile(r"[一-鿿]")

Rect = tuple[float, float, float, float]


@dataclass
class Sheet:
    bbox: Rect
    number: str = ""
    name: str = ""
    kind: str = "other"          # system / plan / notes / other
    source: str = ""             # polyline / insert:<块名> / bounds:<块名>
    page: int = 0                # 预览页码（1 起），渲染后填写
    texts: list[Any] = field(default_factory=list, repr=False)

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]

    def contains(self, x: float, y: float) -> bool:
        b = self.bbox
        return b[0] <= x <= b[2] and b[1] <= y <= b[3]

    def to_json(self) -> dict[str, Any]:
        return {"bbox": [round(v, 2) for v in self.bbox], "number": self.number, "name": self.name,
                "kind": self.kind, "source": self.source, "page": self.page}


# ---------------------------------------------------------------- 几何工具

def _area(r: Rect) -> float:
    return max(r[2] - r[0], 0.0) * max(r[3] - r[1], 0.0)


def _inside(inner: Rect, outer: Rect, tol: float = 0.0) -> bool:
    return (outer[0] - tol <= inner[0] and outer[1] - tol <= inner[1]
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def _rect_points(points: list[tuple[float, float]], tol: float) -> Rect | None:
    """首尾相接的 4 个角点是否围成轴向矩形；是则返回其范围。"""
    pts = list(points)
    if len(pts) >= 5 and abs(pts[0][0] - pts[-1][0]) <= tol and abs(pts[0][1] - pts[-1][1]) <= tol:
        pts = pts[:-1]
    if len(pts) != 4:
        return None
    for k in range(4):
        (ax, ay), (bx, by) = pts[k], pts[(k + 1) % 4]
        if abs(ax - bx) > tol and abs(ay - by) > tol:
            return None
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    return (x0, y0, x1, y1)


def _closed_rect(entity: Any, tol: float, matrix: Any = None) -> Rect | None:
    kind = entity.dxftype()
    try:
        if kind == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
            closed = bool(entity.closed)
        elif kind == "POLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in entity.points()]
            closed = bool(entity.is_closed)
        else:
            return None
    except Exception:
        return None
    if matrix is not None:
        pts = [tuple(matrix.transform((x, y, 0.0)))[:2] for x, y in pts]
    if not closed and not (len(pts) >= 5 and abs(pts[0][0] - pts[-1][0]) <= tol
                           and abs(pts[0][1] - pts[-1][1]) <= tol):
        return None
    return _rect_points(pts, tol)


def _paper_like(r: Rect, min_side: float, max_aspect: float) -> bool:
    w, h = r[2] - r[0], r[3] - r[1]
    short, long_ = min(w, h), max(w, h)
    return short >= min_side and long_ / max(short, 1e-9) <= max_aspect


def _merge_borders(rects: list[Rect], ratio: float) -> list[Rect]:
    """图框通常是内外两道边线（相距几毫米）：互相嵌套且边距很小的视为同一张图，保留外框。"""
    out: list[Rect] = []
    for r in sorted(rects, key=_area, reverse=True):
        w = r[2] - r[0]
        if any(_inside(r, o, tol=0) and _area(r) >= _area(o) * (1 - ratio) ** 2 and
               max(abs(r[0] - o[0]), abs(r[2] - o[2]), abs(r[1] - o[1]), abs(r[3] - o[3])) <= w * ratio
               for o in out):
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------- 主流程

def text_height_basis(texts: list[Any]) -> float:
    hs = [float(t.height) for t in texts if getattr(t, "height", 0) and float(t.height) > 0]
    return statistics.median(hs) if hs else 2.5


def _crossing_count(index: Any, rect: Rect, skip: set[int], tol: float) -> tuple[int, int]:
    """返回 (切到边框的实体数, 完全在框内的实体数)。比框还大的实体（别的图框、整图外框）不算。"""
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    inside = crossing = 0
    for i in index.query((x0 - tol, y0 - tol, x1 + tol, y1 + tol)):
        if i in skip:
            continue
        b = index.boxes[i]
        bw, bh = b[2] - b[0], b[3] - b[1]
        if bw >= w * 0.9 or bh >= h * 0.9:
            continue
        if _inside(b, rect, tol):
            inside += 1
        elif not (b[2] < x0 - tol or b[0] > x1 + tol or b[3] < y0 - tol or b[1] > y1 + tol):
            crossing += 1
    return crossing, inside


def _frame_candidates(doc: Any, index: Any, min_side: float, max_aspect: float, tol: float
                      ) -> tuple[list[tuple[str, list[Rect], int]], list[Rect]]:
    """返回 ([(来源, 幅面变体列表, 实体下标)], 模型空间闭合矩形)。"""
    groups: list[tuple[str, list[Rect], int]] = []
    loose: list[Rect] = []
    variant_cache: dict[str, list] = {}
    for i, e in enumerate(index.entities):
        kind = e.dxftype()
        if kind in ("LWPOLYLINE", "POLYLINE"):
            r = _closed_rect(e, tol)
            if r and _paper_like(r, min_side, max_aspect):
                loose.append(r)
            continue
        if kind != "INSERT":
            continue
        b = index.boxes[i]
        if min(b[2] - b[0], b[3] - b[1]) < min_side:
            continue
        name = str(e.dxf.name)
        if name not in variant_cache:
            blk = doc.blocks.get(name)
            variant_cache[name] = [ent for ent in (blk or []) if ent.dxftype() in ("LWPOLYLINE", "POLYLINE")]
        try:
            m = e.matrix44()
        except Exception:
            m = None
        variants = []
        for ent in variant_cache[name]:
            r = _closed_rect(ent, tol, matrix=m) if m is not None else None
            if r and _paper_like(r, min_side, max_aspect):
                variants.append(r)
        variants = _merge_borders(variants, float(_CFG["border_merge_ratio"]))
        if variants:
            groups.append((f"insert:{name}", variants, i))
        elif _paper_like(b, min_side, max_aspect):
            groups.append((f"bounds:{name}", [b], i))
    return groups, loose


def _numbers_in(rect: Rect, catalog_hits: list[Any]) -> set[str]:
    return {t.text.strip() for t in catalog_hits
            if rect[0] <= t.x <= rect[2] and rect[1] <= t.y <= rect[3]}


def _pick_variant(index: Any, variants: list[Rect], skip: set[int], tol: float,
                  catalog_hits: list[Any]) -> Rect:
    """同一个图框块的幅面变体里挑真实幅面。

    边框不切内容（或切边最少）、且最多装下一个目录图号的变体才合格。
    按面积从小到大评估，采用实体边际增益原则：从最小的合格变体开始，
    只有当下级更大变体能带来显著（>= 15%）的图元增量且不增加切边惩罚时，
    才向外扩展；进入增量平缓期（增量 < 15%，仅多出空白或相邻图纸杂乱边界）
    直接收敛在最小有效幅面上，防止把相邻图纸卷入。
    """
    allowed = float(_CFG["crossing_allowance_ratio"])
    scored = []
    for r in variants:
        crossing, inside = _crossing_count(index, r, skip, tol)
        scored.append((crossing, inside, _area(r), r, len(_numbers_in(r, catalog_hits))))
    single = [s for s in scored if s[4] <= 1] or scored
    good = [s for s in single if s[0] <= max(3, allowed * s[1])]
    if good:
        good.sort(key=lambda s: s[2])
        best = good[0]
        for nxt in good[1:]:
            d_area = nxt[2] - best[2]
            d_ins = nxt[1] - best[1]
            if d_area <= 0 or d_ins <= 0:
                continue
            marginal_density = d_ins / d_area
            base_density = best[1] / max(best[2], 1e-6)
            eff = marginal_density / max(base_density, 1e-9)
            gain = d_ins / max(best[1], 1)
            # 只有增量明显（>= 15%）、新增区域密度充实（eff >= 0.35）且无严重穿边时才扩展
            if gain >= 0.15 and eff >= 0.35 and nxt[0] <= best[0] + 1:
                best = nxt
        return best[3]
    # 平面图的建筑线常画出图框，所有变体都切内容：取切得最少的，同样只在装一个图号的变体里挑
    return min(single, key=lambda s: (s[0], s[2]))[3]


def _resolve_nesting(items: list[tuple[Rect, str]], catalog_hits: list[Any]) -> list[tuple[Rect, str]]:
    """嵌套的框：外框里装着两个以上带不同目录图号的子框时，外框是排版外框，去掉它；
    否则子框是图签格 / 详图框，去掉子框。"""
    items = sorted(items, key=lambda it: _area(it[0]), reverse=True)
    drop: set[int] = set()
    for i, (outer, _src) in enumerate(items):
        children = [j for j, (r, _s) in enumerate(items) if j != i and _inside(r, outer) and _area(r) < _area(outer)]
        numbered = {n for j in children for n in _numbers_in(items[j][0], catalog_hits)}
        if len(numbered) >= 2:
            drop.add(i)
    kept = [it for k, it in enumerate(items) if k not in drop]
    out: list[tuple[Rect, str]] = []
    for r, src in kept:
        if any(_inside(r, o, tol=0) for o, _s in out):
            continue
        out.append((r, src))
    return out


def _overlap_ratio(a: Rect, b: Rect) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy / max(min(_area(a), _area(b)), 1e-9)


def _classify(name: str) -> str:
    t = re.sub(r"\s+", "", name or "")
    if any(m in t for m in SYSTEM_MARKERS):
        return "system"
    if any(m in t for m in NOTE_MARKERS):
        return "notes"
    if any(m in t for m in PLAN_MARKERS):
        return "plan"
    return "other"


def build_catalog(texts: list[Any], h_basis: float) -> tuple[dict[str, str], set[int]]:
    """图纸目录：同一前缀、末段为序号的图号在一列里紧密竖排三个以上（目录表的图号列，行距在几个字高内），
    同一行右侧最近的中文是图名。

    返回 ({图号: 图名}, 目录表里那些图号文字的 id)。通过特征词过滤与多数决机制，
    彻底剔除端子排、接线表等非图纸目录列。
    """
    VALID_DOC_MARKERS = ("图", "表", "说明", "目录", "详图", "大样", "布置")
    INVALID_PREFIXES = ("至", "由", "接", "引自")

    cands = [t for t in texts if SHEET_NO_RE.match(t.text.strip().upper()) and not DATE_RE.match(t.text.strip())
             and re.search(r"[-_]\d+$", t.text.strip())]
    columns: dict[tuple[str, int], list[Any]] = {}
    for t in cands:
        key = t.text.strip()
        prefix = re.split(r"[-_](?=[^-_]*$)", key)[0]
        col = round(t.x / max(h_basis, 1e-6))
        columns.setdefault((prefix, col), []).append(t)
    catalog: dict[str, str] = {}
    table_ids: set[int] = set()
    max_gap = float(_CFG["catalog_row_gap_text_heights"]) * h_basis
    runs: list[list[Any]] = []
    for items in columns.values():
        items = sorted(items, key=lambda t: -t.y)
        run = [items[0]]
        for t in items[1:]:
            if run[-1].y - t.y <= max_gap:
                run.append(t)
            else:
                runs.append(run)
                run = [t]
        runs.append(run)
    for run in runs:
        if len({t.text.strip() for t in run}) < int(_CFG["catalog_min_rows"]):
            continue
        matched_pairs: list[tuple[Any, str]] = []
        for occ in run:
            tol = max(occ.height, h_basis) * 0.6
            row = [o for o in texts if abs(o.y - occ.y) <= tol and o.x > occ.x
                   and CJK_RE.search(o.text) and 2 <= len(o.text.strip()) <= 40]
            if not row:
                continue
            clean_row = [o for o in row if not any(o.text.strip().startswith(p) for p in INVALID_PREFIXES)]
            doc_like = [o for o in clean_row if any(m in o.text for m in VALID_DOC_MARKERS)]
            best_text = min(doc_like or clean_row, key=lambda o: o.x - occ.x, default=None)
            if best_text:
                matched_pairs.append((occ, best_text.text.strip()))

        if not matched_pairs:
            continue
        valid_doc_count = sum(1 for _, name in matched_pairs if any(m in name for m in VALID_DOC_MARKERS))
        if valid_doc_count / len(matched_pairs) < 0.4:
            continue

        for occ, name in matched_pairs:
            catalog.setdefault(occ.text.strip(), name)
            table_ids.add(id(occ))
    return catalog, table_ids


def _name_sheet(s: Sheet, catalog: dict[str, str], table_ids: set[int]) -> None:
    """图号：框内的目录图号（目录表本身那一列不算），离右侧角点最近的一个；
    没有目录时取图签区（右下）里形如图号的文字。图名：目录同一行；否则图签里带图类关键词的大字。"""
    x0, y0, x1, y1 = s.bbox
    own = [t for t in s.texts if t.text.strip() in catalog and id(t) not in table_ids]
    if own:
        best = min(own, key=lambda t: (x1 - t.x) + min(t.y - y0, y1 - t.y))
        s.number = best.text.strip()
        s.name = catalog[s.number]
    else:
        tb_x, tb_y = float(_CFG["title_block_x_ratio"]), float(_CFG["title_block_y_ratio"])
        in_tb = [t for t in s.texts if t.x >= x0 + tb_x * s.width and t.y <= y0 + tb_y * s.height]
        nums = [t for t in in_tb if SHEET_NO_RE.match(t.text.strip().upper()) and not DATE_RE.match(t.text.strip())]
        if nums:
            s.number = min(nums, key=lambda t: (x1 - t.x) + (t.y - y0)).text.strip()
        pool = [t for t in in_tb if CJK_RE.search(t.text) and 2 <= len(t.text.strip()) <= 30]
        marked = [t for t in pool if _classify(t.text) != "other"]
        pick = max(marked or pool, key=lambda t: (t.height, len(t.text)), default=None)
        s.name = pick.text.strip() if pick else ""
    s.kind = _classify(s.name)


def detect_sheets(doc: Any, index: Any, texts: list[Any]) -> list[Sheet]:
    """识别全部图幅；texts 为模型空间全部文字（native_takeoff.TextRecord，含块内文字，字高已按比例缩放）。"""
    h_basis = text_height_basis(texts)
    min_side = float(_CFG["min_side_text_heights"]) * h_basis
    max_aspect = float(_CFG["max_aspect"])
    tol = max(h_basis * float(_CFG["edge_tol_text_heights"]), 1e-6)

    catalog, table_ids = build_catalog(texts, h_basis)
    # 图签里的图号（目录表那一列本身不代表所在图幅）
    title_hits = [t for t in texts if t.text.strip() in catalog and id(t) not in table_ids]

    groups, loose = _frame_candidates(doc, index, min_side, max_aspect, tol)
    loose_idx = {i for i, e in enumerate(index.entities)
                 if e.dxftype() in ("LWPOLYLINE", "POLYLINE")
                 and min(index.boxes[i][2] - index.boxes[i][0], index.boxes[i][3] - index.boxes[i][1]) >= min_side}
    chosen: list[tuple[Rect, str]] = []
    for src, variants, ent_i in groups:
        chosen.append((_pick_variant(index, variants, {ent_i} | loose_idx, tol, title_hits), src))
    for r in _merge_borders(loose, float(_CFG["border_merge_ratio"])):
        chosen.append((r, "polyline"))
    chosen = _resolve_nesting(_dedupe(chosen), title_hits)

    sheets = [Sheet(bbox=r, source=src) for r, src in chosen]
    for s in sheets:
        s.texts = [t for t in texts if s.contains(t.x, t.y)]
    sheets = [s for s in sheets if s.texts]
    for s in sheets:
        _name_sheet(s, catalog, table_ids)
    # 同一图号只留一张：同一张图被两个框认领时留面积小的（另一个是相邻图框的大变体）
    best_by_no: dict[str, Sheet] = {}
    for s in sheets:
        if s.number and (s.number not in best_by_no or _area(s.bbox) < _area(best_by_no[s.number].bbox)):
            best_by_no[s.number] = s
    sheets = [s for s in sheets if not s.number or best_by_no[s.number] is s or s.kind == "notes"]
    # 剔除执业印章/审图章等图面图章框
    STAMP_WORDS = ("此处盖", "出图章", "印章", "签署", "审图章", "注册执业")
    sheets = [s for s in sheets if not any(w in s.name for w in STAMP_WORDS)]
    # 没有图号（有目录时：图号不在目录里）、又压在有图号图幅上的框（平面底图块、区位索引图）不是独立图纸
    def _numbered(s: Sheet) -> bool:
        return bool(s.number) and (not catalog or s.number in catalog)
    numbered = [s for s in sheets if _numbered(s)]
    if numbered:
        sheets = [s for s in sheets if not s.source.startswith("bounds:") or _numbered(s) or s.kind != "other"]
    ratio = float(_CFG["unnumbered_overlap_drop"])
    sheets = [s for s in sheets if _numbered(s) or not any(_overlap_ratio(s.bbox, o.bbox) > ratio for o in numbered)]
    sheets.sort(key=lambda s: (s.number == "", s.number, -round(s.bbox[3]), s.bbox[0]))
    return sheets


def _dedupe(items: list[tuple[Rect, str]]) -> list[tuple[Rect, str]]:
    out: list[tuple[Rect, str]] = []
    for r, src in items:
        w = r[2] - r[0]
        if any(max(abs(r[k] - o[k]) for k in range(4)) <= w * float(_CFG["border_merge_ratio"]) for o, _s in out):
            continue
        out.append((r, src))
    return out
